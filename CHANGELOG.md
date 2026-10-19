# Changelog

## [0.3.0]

- Tooltips for every setting (Roll, Name, Parent, Armature, Panel Category).
- Fix: redoing an op (Adjust Last Operation) placed bones around the object origin.
- `pack.sh --validate` runs Blender's extension validator on the zip.

## [0.2.0]

- Span: bone along the selection's long axis (cylinder end to end), or refit the active bone.
- Head: move the active bone's head. Head/Tail drag connected neighbors along.
- Pie menu on Shift+Alt+B (mesh edit); in armature edit the same key flips back.
- Flip between mesh edit and the armature's edit mode.
- Active bone and Parent dropdowns in the sidebar; new bones auto-parent to Parent.
- Name, parent, roll (and Span invert/target) editable in the Adjust Last Operation panel; replaces the rename popup.

## [0.1.1]

- Operators renamed to `mesh.easybones_*`; right-click > Assign Shortcut now works.
- New / Extend pop up an F2-style rename field on the created bone instead of a dialog.
- Name suggestion comes from the active bone (Extend increments it: `finger.01.L` -> `finger.02.L`).
- Unbound keymap entries under Preferences > Keymap > Mesh.
- Addon preference for the sidebar tab (default: Rigging).

## [0.1.0]

- Initial release: New, Tail, Extend operators, roll mode, sidebar panel in mesh edit mode.
