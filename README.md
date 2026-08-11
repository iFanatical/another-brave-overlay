<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brave_dark.png">
    <source media="(prefers-color-scheme: light)" srcset="brave_light.png">
    <img alt="Brave Logo" src="brave_light.png">
  </picture>
</p>

another-brave-overlay
=====================

![⚙️ Update](https://img.shields.io/github/actions/workflow/status/falbrechtskirchinger/another-brave-overlay/update-ebuilds.yml?style=flat-square&logo=github&label=%E2%9A%99%EF%B8%8F%20Update)
![🧪 Merge (stable)](https://img.shields.io/github/actions/workflow/status/falbrechtskirchinger/another-brave-overlay/test-stable.yml?style=flat-square&logo=github&label=%F0%9F%A7%AA%20Merge%20(stable))
![🧪 Merge (beta)](https://img.shields.io/github/actions/workflow/status/falbrechtskirchinger/another-brave-overlay/test-beta.yml?style=flat-square&logo=github&label=%F0%9F%A7%AA%20Merge%20(beta))
![🧪 Merge (nightly)](https://img.shields.io/github/actions/workflow/status/falbrechtskirchinger/another-brave-overlay/test-nightly.yml?style=flat-square&logo=github&label=%F0%9F%A7%AA%20Merge%20(nightly))
![🔍 Divergence](https://img.shields.io/github/actions/workflow/status/falbrechtskirchinger/another-brave-overlay/check-src-ebuilds.yml?style=flat-square&logo=github&label=%F0%9F%94%8D%20Divergence)

> **Fork note.** This is a fork of
> [falbrechtskirchinger/another-brave-overlay](https://github.com/falbrechtskirchinger/another-brave-overlay)
> that adds packages for **Brave Origin**. Everything else is unchanged and
> tracks upstream. The CI badges above report upstream's status, not this
> fork's.

This Gentoo overlay provides automatically generated ebuilds for the [Brave browser](https://brave.com/), a privacy-focused, open-source web browser based on Chromium. The ebuilds are based on the official Gentoo Google Chrome ebuilds, which are routinely checked for divergence, and adapted to deliver the stable, beta, and nightly versions of Brave via the following ebuilds:

- `www-client/brave-browser` (stable)
- `www-client/brave-browser-beta` (beta)
- `www-client/brave-browser-nightly` (nightly)

[Brave Origin](https://brave.com/origin/) — the minimalist build that keeps Shields but drops Leo, News, Playlist, Rewards, Speedreader, Talk, Tor, VPN, Wallet, the Wayback Machine, the Web Discovery Project, and email aliases, along with the daily usage ping, crash reporting, and P3A analytics — is packaged separately:

- `www-client/brave-origin` (stable)
- `www-client/brave-origin-beta` (beta)
- `www-client/brave-origin-nightly` (nightly)

Origin is free on Linux. It keeps its profile in its own directory under `~/.config/BraveSoftware/`, so it can be installed side by side with `www-client/brave-browser` without sharing data. Its launcher is `/usr/bin/brave-origin-stable` for the stable channel, and `/usr/bin/brave-origin-beta` / `/usr/bin/brave-origin-nightly` for the pre-release channels.

These ebuilds install pre-built binary versions of Brave, similar to the Google Chrome ebuilds in the main Gentoo repository.

Installation
------------

To install Brave from this overlay, follow these steps:

1. Add the overlay:

    ```sh
    sudo eselect repository add another-brave-overlay git https://github.com/iFanatical/another-brave-overlay.git
    ```

2. Sync the overlay to download the ebuilds:

    ```sh
    emaint sync -r another-brave-overlay
    ```

3. Install Brave from the desired release channel using `emerge`. Available packages are:

    - `www-client/brave-browser` (stable)
    - `www-client/brave-browser-beta` (beta)
    - `www-client/brave-browser-nightly` (nightly)
    - `www-client/brave-origin` (stable)
    - `www-client/brave-origin-beta` (beta)
    - `www-client/brave-origin-nightly` (nightly)

    For example, to install the stable version:

    ```sh
    emerge -av www-client/brave-browser
    ```

    Or, for Brave Origin:

    ```sh
    emerge -av www-client/brave-origin
    ```

    The beta and nightly Origin packages are `~amd64`, so accept them first:

    ```sh
    echo 'www-client/brave-origin-nightly ~amd64' >> /etc/portage/package.accept_keywords/brave
    ```

Keeping up with new releases
----------------------------

New upstream versions are picked up by `scripts/update_ebuilds.py`, which reads
the [brave/brave-browser releases](https://github.com/brave/brave-browser/releases),
verifies each release's checksum signature with Brave's OpenPGP key, writes a
new ebuild, and regenerates the `Manifest`:

```sh
# Add ebuilds for any new releases, committing each one
python scripts/update_ebuilds.py -v --update --commit

# Drop superseded ebuilds, keeping only the newest per package
python scripts/update_ebuilds.py -v --prune
```

Useful flags:

- `--product brave-origin` limits the run to one package family; repeat the
  flag for more. Without it, both `brave-browser` and `brave-origin` are
  updated.
- `--manifest` recomputes every `Manifest` from the ebuilds currently present,
  which is what you want after adding or removing an ebuild by hand.

A release is only picked up once Brave clears the *pre-release* flag on the
GitHub release, so the newest tag on GitHub is typically a version or two ahead
of what lands here. That is deliberate: it is the same gate the
`brave-browser` packages use.

To have this run unattended, drive it from a systemd timer or cron on the
machine that hosts your clone of the overlay, then sync and update as usual:

```sh
cd /path/to/this/overlay \
  && python scripts/update_ebuilds.py --update --commit \
  && python scripts/update_ebuilds.py --prune
emaint sync -r another-brave-overlay && emerge -avuDN @world
```

### eix

No configuration is needed: `eix-update` indexes every repository listed in
`repos.conf`, so these packages show up in `eix brave-origin` as soon as the
overlay is configured, and `app-portage/eix` installs a `postsync.d` hook that
reindexes after every `emaint sync`.

The one case the hook does not cover is running `update_ebuilds.py` against a
local clone without syncing — the ebuilds change under eix's feet. Follow it
with an explicit reindex:

```sh
eix-update
```

**When editing these ebuilds, keep `KEYWORDS` a literal assignment.** Unlike
portage, eix reads ebuilds with a textual parser rather than sourcing them, so
it takes the last `KEYWORDS=` line it sees. Assigning it inside a conditional
makes eix report the wrong stability — a stable package shows up as `~amd64` —
even though portage resolves it correctly. That is why the three Origin ebuilds
are identical except for their `KEYWORDS` line, matching the `brave-browser`
ebuilds.

Note that `brave-origin-beta` and `brave-origin-nightly` are amd64-only. Brave
publishes arm64 `.deb` packages for those two channels only sporadically, and
requiring them would stall updates indefinitely.

Issues and Contributions
------------------------

If you encounter any problems or have suggestions for improvement, please open an issue on the GitHub repository: https://github.com/falbrechtskirchinger/another-brave-overlay.

**Please do not submit pull requests at this time.**

The underlying automation is being completely rewritten. As of May 2026, I don't have a firm timeline, but I aim to have the rewrite finished before the end of the year.

License
-------

Ebuilds in this overlay are licensed under the GNU General Public License v2.0 (GPL-2). Scripts and other files are licensed under the MIT License (MIT).

See the `LICENSE.GPL-2` and `LICENSE.MIT` files in the repository for full details.

**Trademark Notice**: All Brave logos, marks, and designations are trademarks or registered trademarks of Brave Software, Inc.
