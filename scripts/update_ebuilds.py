#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2025-2026 Florian Albrechtskirchinger <falbrechtskirchinger@gmail.com>
#
# SPDX-License-Identifier: MIT

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime

import requests
from shared import (
    CHANNELS,
    CHANNELS_WITH_TITLE,
    collect_test_results,
    extract_version,
    get_ebuilds,
    gh_get,
    make_name_from_channel,
    require_gha,
)

ARCHES = ("amd64", "arm64")
BRAVE_RELEASES = "https://api.github.com/repos/brave/brave-browser/releases"
BRAVE_SOURCE_FILE = "{name}_{version}_{arch}.deb"
BRAVE_SOURCE_URL = f"https://github.com/brave/brave-browser/releases/download/v{{version}}/{BRAVE_SOURCE_FILE}"
EBUILD_FILE = "{name}-{version}.ebuild"
EBUILD_FILE_PATH = f"www-client/{{name}}/{EBUILD_FILE}"
MANIFEST_HASH_ALGOS = ("BLAKE2B", "SHA512")

SIGNING_KEYS = {
    "stable": "brave-browser-release.asc",
    "beta": "brave-browser-pre-release.asc",
    "nightly": "brave-browser-pre-release.asc",
}

# Package families tracked by this overlay. Both are published as .deb assets
# on the same brave/brave-browser releases.
PRODUCTS = ("brave-browser", "brave-origin")

# Brave publishes arm64 .deb packages for the Origin pre-release channels only
# sporadically. Requiring them would stall updates indefinitely, so those two
# packages are amd64-only (which matches their KEYWORDS).
PRODUCT_ARCHES = {
    ("brave-origin", "beta"): ("amd64",),
    ("brave-origin", "nightly"): ("amd64",),
}


def get_arches(product, channel):
    return PRODUCT_ARCHES.get((product, channel), ARCHES)


def product_from_name(name):
    """Map a package name (e.g. brave-origin-beta) to its product family."""
    for product in sorted(PRODUCTS, key=len, reverse=True):
        if name == product or name.startswith(f"{product}-"):
            return product
    raise ValueError(f"Unknown product for package '{name}'.")


def product_from_path(path):
    return product_from_name(os.path.basename(os.path.dirname(path)))


def verify_release_signature(channel, version, name):
    key_file = SIGNING_KEYS.get(channel)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(base_dir, "data", key_file)

    sha_url = (
        BRAVE_SOURCE_URL.format(name=name, version=version, arch="amd64") + ".sha256"
    )
    asc_url = sha_url + ".asc"

    try:
        print(f"Fetching signature files for {name} {version}...")
        response_sha = requests.get(sha_url)
        response_asc = requests.get(asc_url)
    except requests.exceptions.HTTPError as e:
        print(f"Failed to download signature files: {e}")
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        gnupg_home = os.path.join(tmpdir, "gnupg")
        os.makedirs(gnupg_home, mode=0o700)

        sha_path = os.path.join(tmpdir, "temp.sha256")
        asc_path = os.path.join(tmpdir, "temp.sha256.asc")

        with open(sha_path, "wb") as f:
            f.write(response_sha.content)
        with open(asc_path, "wb") as f:
            f.write(response_asc.content)

        # Import key
        subprocess.run(
            ["gpg", "--homedir", gnupg_home, "--import", key_path],
            check=True,
            capture_output=True,
        )

        # Verify signature
        res = subprocess.run(
            ["gpg", "--homedir", gnupg_home, "--verify", asc_path, sha_path],
            capture_output=True,
        )

        if res.returncode != 0:
            print(f"Signature verification failed for {name} {version}")
            return False

        return True


def print_rate_limit(res):
    limit = res.headers.get("X-RateLimit-Limit")
    remaining = res.headers.get("X-RateLimit-Remaining")
    reset_time = res.headers.get("X-RateLimit-Reset")
    if limit:
        limit = f"Rate limit: {limit}"
    if remaining:
        remaining = f"Remaining: {remaining}"
    if reset_time:
        reset_time = datetime.fromtimestamp(int(reset_time))
        reset_time = f"Resets at: {reset_time}"

    msg = " | ".join(filter(None, (limit, remaining, reset_time)))
    if msg:
        print(msg)


def handle_rate_limit(res):
    if res.status_code == 403 and "rate limit exceeded" in res.text.lower():
        print("Rate limit exceeded!")
        print_rate_limit(res)
        return True
    return False


def get_latest_releases(products=PRODUCTS):
    releases = {
        product: {channel: None for channel, _ in CHANNELS_WITH_TITLE}
        for product in products
    }
    releases_wanted = len(products) * len(CHANNELS)
    releases_found = 0
    page = 0
    MAX_PAGES = 5
    url = BRAVE_RELEASES
    while url and page < MAX_PAGES:
        try:
            print(f"Fetching page {page+1} of releases...")
            response = gh_get(url)
            print_rate_limit(response)
        except requests.exceptions.HTTPError as e:
            res = e.response
            if handle_rate_limit(res):
                break
            else:
                raise e

        for release in response.json():
            if release["prerelease"]:
                continue

            for channel, title in CHANNELS_WITH_TITLE:
                if not release["name"].startswith(title):
                    continue

                tag = release["tag_name"]
                assert tag[0] == "v"
                version = tag[1:]
                asset_files = {asset["name"] for asset in release["assets"]}

                for product in products:
                    if releases[product][channel]:
                        continue

                    name = make_name_from_channel(channel, base_name=product)
                    required_assets = {
                        BRAVE_SOURCE_FILE.format(name=name, version=version, arch=arch)
                        for arch in get_arches(product, channel)
                    }
                    if required_assets.issubset(
                        asset_files
                    ) and verify_release_signature(channel, version, name):
                        releases[product][channel] = version
                        releases_found += 1

            if releases_found == releases_wanted:
                break

        if releases_found == releases_wanted:
            break

        url = response.links.get("next", {}).get("url")
        page += 1

    if releases_found != releases_wanted:
        missing = [
            f"{make_name_from_channel(channel, base_name=product)}"
            for product in products
            for channel in CHANNELS
            if not releases[product][channel]
        ]
        raise RuntimeError(
            f"Could not find latest release for: {', '.join(missing)}."
        )

    return releases


def get_new_releases(releases, repo_dir=None):
    new_releases = dict()
    for product, channels in releases.items():
        for channel, version in channels.items():
            ebuilds, _ = get_ebuilds(channel, base_name=product, repo_dir=repo_dir)
            ebuild_versions = {extract_version(ebuild) for ebuild in ebuilds}
            if version not in ebuild_versions:
                new_releases.setdefault(product, {})[channel] = version

    return new_releases


def update_manifest(ebuild_dir, name, arches=ARCHES):
    ebuilds = glob.glob(os.path.join(ebuild_dir, "*.ebuild"))
    versions = set(extract_version(ebuild) for ebuild in ebuilds)
    files_in_manifest = set()
    sources = [
        {
            "file": BRAVE_SOURCE_FILE.format(name=name, version=version, arch=arch),
            "url": BRAVE_SOURCE_URL.format(name=name, version=version, arch=arch),
            "version": version,
        }
        for version in versions
        for arch in arches
    ]
    sources_by_filename = {source["file"]: source for source in sources}
    manifest_path = os.path.join(ebuild_dir, "Manifest")
    # A newly added package has no Manifest yet; start from an empty one.
    if os.path.exists(manifest_path):
        with open(manifest_path, "r") as f:
            lines = f.readlines()
    else:
        lines = []

    new_lines = []
    for line in lines:
        parts = line.split(" ")
        if parts[0] == "DIST":
            if parts[1] in sources_by_filename:
                # Keep DIST lines for current ebuilds
                new_lines.append(line)
                files_in_manifest.add(parts[1])
            elif parts[1].endswith(".sha256"):
                # Keep DIST lines for associated checksum files
                if parts[1][: -len(".sha256")] in sources_by_filename:
                    new_lines.append(line)
            elif parts[1].endswith(".sha256.asc"):
                # Keep DIST lines for associated checksum signature files
                if parts[1][: -len(".sha256.asc")] in sources_by_filename:
                    new_lines.append(line)
        else:
            new_lines.append(line)

    def add_hash(url, filename):
        hashers = {algo: hashlib.new(algo.lower()) for algo in MANIFEST_HASH_ALGOS}
        size = 0
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=8192):
                size += len(chunk)
                for hasher in hashers.values():
                    hasher.update(chunk)

        digests = {algo: hasher.hexdigest() for algo, hasher in hashers.items()}
        new_lines.append(
            f"DIST {filename} {size} {' '.join([f'{algo} {digest}' for algo, digest in digests.items()])}\n"
        )

    # Add DIST lines for missing files
    for source in sources:
        if source["file"] not in files_in_manifest:
            add_hash(source["url"], source["file"])
            add_hash(source["url"] + ".sha256", source["file"] + ".sha256")
            add_hash(source["url"] + ".sha256.asc", source["file"] + ".sha256.asc")

    with open(manifest_path, "w") as f:
        f.writelines(sorted(new_lines))


def add_ebuilds_for_new_releases(new_releases, repo_dir, commit_changes=False):
    new_ebuilds = dict()
    for product, channels in new_releases.items():
        for channel, version in channels.items():
            ebuilds, ebuild_dir = get_ebuilds(
                channel, base_name=product, repo_dir=repo_dir, only_latest=True
            )
            if len(ebuilds) == 0:
                raise RuntimeError(
                    f"No ebuilds for '{product}' release channel '{channel}'."
                )
            latest_ebuild = ebuilds[0]
            name = make_name_from_channel(channel, base_name=product)
            filename = EBUILD_FILE.format(name=name, version=version)
            new_ebuild = os.path.join(ebuild_dir, filename)

            shutil.copy(latest_ebuild, new_ebuild)
            update_manifest(ebuild_dir, name, arches=get_arches(product, channel))
            new_ebuilds.setdefault(product, {}).setdefault(channel, []).append(version)

            if commit_changes:
                subprocess.run(
                    ["git", "add", new_ebuild, os.path.join(ebuild_dir, "Manifest")],
                    check=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"www-client/{name}: added {version}"],
                    check=True,
                )

    return new_ebuilds


def update_ebuilds(repo_dir=None, commit_changes=False, products=PRODUCTS):
    repo_dir = repo_dir or os.getcwd()

    releases = get_latest_releases(products=products)
    new_releases = get_new_releases(releases, repo_dir)
    return add_ebuilds_for_new_releases(
        new_releases, repo_dir, commit_changes=commit_changes
    )


def regen_manifests(repo_dir=None, products=PRODUCTS):
    """Recompute every Manifest from the ebuilds currently present."""
    repo_dir = repo_dir or os.getcwd()

    for product in products:
        for channel in CHANNELS:
            name = make_name_from_channel(channel, base_name=product)
            ebuilds, ebuild_dir = get_ebuilds(
                channel, base_name=product, repo_dir=repo_dir
            )
            if not ebuilds:
                continue
            print(f"Hashing distfiles for www-client/{name}...")
            update_manifest(ebuild_dir, name, arches=get_arches(product, channel))


def prune_ebuilds(
    repo_dir=None, commit_changes=False, successful_channels_only=False, products=PRODUCTS
):
    repo_dir = repo_dir or os.getcwd()

    pruned_ebuilds = dict()

    if successful_channels_only:
        # Only CI-tested packages are eligible; anything without a test result
        # (e.g. a product this overlay's workflows don't build) is left alone.
        test_results = collect_test_results(from_event=False)
        targets = [
            (product_from_path(result["ebuild_path"]), channel)
            for channel, result in test_results.items()
            if result["conclusion"] == "success"
        ]
        targets = [t for t in targets if t[0] in products]
    else:
        targets = [(product, channel) for product in products for channel in CHANNELS]

    for product, channel in targets:
        ebuilds, ebuild_dir = get_ebuilds(channel, base_name=product, repo_dir=repo_dir)

        if len(ebuilds) > 1:
            dropped = []
            name = make_name_from_channel(channel, base_name=product)
            for ebuild in ebuilds[:-1]:
                if commit_changes:
                    subprocess.run(["git", "rm", ebuild], check=True)
                else:
                    os.unlink(ebuild)
                version = extract_version(ebuild)
                dropped.append(version)
                pruned_ebuilds.setdefault(product, {}).setdefault(channel, []).append(
                    version
                )

            update_manifest(ebuild_dir, name, arches=get_arches(product, channel))

            if commit_changes:
                subprocess.run(
                    ["git", "add", os.path.join(ebuild_dir, "Manifest")], check=True
                )
                subprocess.run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"www-client/{name}: dropped {', '.join(dropped)}",
                    ],
                    check=True,
                )

    return pruned_ebuilds


def write_step_summary(title, ebuilds=None):
    require_gha()

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        raise RuntimeError("GITHUB_STEP_SUMMARY environment variable unset or empty.")

    with open(summary_file, "a") as f:
        f.write(f"### {title}\n\n")
        if ebuilds:
            for product in PRODUCTS:
                if product not in ebuilds:
                    continue
                for channel in CHANNELS:  # Iterate ebuilds in channel order
                    if channel not in ebuilds[product]:
                        continue
                    name = make_name_from_channel(channel, base_name=product)
                    for version in ebuilds[product][channel]:
                        f.write(
                            f"- **{channel.capitalize()}**: `www-client/{name}-{version}`\n"
                        )
            f.write("\n")


def write_step_summary_new_ebuilds(new_ebuilds):
    if new_ebuilds:
        write_step_summary("✨ New ebuilds were added:", new_ebuilds)
    else:
        write_step_summary("⚪ No new ebuilds were added!")


def write_step_summary_pruned_ebuilds(pruned_ebuilds):
    if pruned_ebuilds:
        write_step_summary("🧹 Old ebuilds were removed:", pruned_ebuilds)
    else:
        write_step_summary("⚪ No ebuilds were pruned!")


def main():
    parser = argparse.ArgumentParser(
        description="Update ebuilds for Brave browser releases."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--update",
        action="store_true",
        help="Check for new releases and update ebuilds.",
    )
    group.add_argument(
        "--prune",
        action="store_true",
        help="Prune old ebuilds.",
    )
    group.add_argument(
        "--manifest",
        action="store_true",
        help="Regenerate the Manifest of every package from the ebuilds present.",
    )
    parser.add_argument(
        "--product",
        action="append",
        choices=PRODUCTS,
        metavar="NAME",
        help=(
            "Limit the operation to a product family "
            f"({', '.join(PRODUCTS)}). May be repeated. Defaults to all."
        ),
    )
    parser.add_argument(
        "--prune-checked",
        action="store_true",
        help="Prune only if channel was tested successfully.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit changes to the repository.",
    )
    parser.add_argument(
        "--step-summary",
        action="store_true",
        help="Write a GitHub step summary.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output.",
    )
    args = parser.parse_args()

    new_ebuilds = None
    pruned_ebuilds = None
    repo_dir = os.path.join(os.path.dirname(__file__), "..")
    products = tuple(args.product) if args.product else PRODUCTS

    if args.manifest:
        regen_manifests(repo_dir=repo_dir, products=products)

    if args.update:
        new_ebuilds = update_ebuilds(
            repo_dir=repo_dir, commit_changes=args.commit, products=products
        )
        if args.verbose:
            print(json.dumps(new_ebuilds, indent=2))
        if args.step_summary:
            write_step_summary_new_ebuilds(new_ebuilds)

    if args.prune:
        pruned_ebuilds = prune_ebuilds(
            repo_dir=repo_dir,
            commit_changes=True,
            successful_channels_only=args.prune_checked,
            products=products,
        )
        if args.verbose:
            print(json.dumps(pruned_ebuilds, indent=2))
        if args.step_summary:
            write_step_summary_pruned_ebuilds(pruned_ebuilds)


if __name__ == "__main__":
    main()
