# EasyBones

Blender add-on (5.0+) for placing bones from vertex selections without leaving mesh edit mode.

## Install

Download `easybones_v*.zip` from [Releases](https://github.com/defaultgnome/easybones/releases), then in Blender: Edit > Preferences > Get Extensions > ⌄ > Install from Disk.

## Use

In mesh edit mode, open the sidebar (N) > **Rigging** tab, pick the target armature, then select vertices:

- **New** — new unparented bone, head at the selection center.
- **Tail** — move the active bone's tail to the selection center.
- **Extend** — new bone connected to the active bone's tail, tail at the selection center. Name auto-increments (`finger.01.L` -> `finger.02.L`).

New / Extend pop up a rename field; Enter confirms. **Roll** applies one of Blender's Recalculate Roll modes to each new/moved bone (Default leaves it alone).

Shortcuts: Preferences > Keymap > Mesh > `EasyBones New/Tail/Extend`, or right-click a button > Assign Shortcut. Sidebar tab name is set in the add-on preferences.

## Develop

```sh
./pack.sh   # -> dist/easybones_v<version>.zip
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python test_easybones.py
```

## Release

1. Bump `version` in `blender_manifest.toml` and add a `## [x.y.z]` section to `CHANGELOG.md`.
2. Push, then Actions > **release** > Run workflow. It tags `vx.y.z` and publishes a release with the zip and changelog notes.

## License

GPL-3.0-or-later
