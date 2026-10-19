import math
import re

import bmesh
import bpy
import numpy as np
from mathutils import Vector

TEMP_TAIL = Vector((0, 0, 0.1))  # world-space offset for a fresh bone's tail
EPS = 1e-6  # zero-length bones get deleted by Blender on leaving edit mode
HORIZONTAL = math.sin(math.radians(15))  # span axis flatter than this counts as horizontal


class Abort(Exception):
    """Raised inside edit_armature before any bone is touched; reported by the op."""


def selected_points(context):
    """World-space positions of selected verts across all meshes in edit mode."""
    pts = []
    for ob in context.objects_in_mode:
        if ob.type != 'MESH':
            continue
        mw = ob.matrix_world
        pts += [mw @ v.co for v in bmesh.from_edit_mesh(ob.data).verts if v.select]
    return pts


def bbox_center(pts):
    lo = Vector(min(p[i] for p in pts) for i in range(3))
    hi = Vector(max(p[i] for p in pts) for i in range(3))
    return (lo + hi) / 2


def span(pts, origin):
    """(head, tail) along the principal axis of pts, through their centroid, out to the extremes.
    Head is the lower end; for a near-horizontal axis, the end nearer origin."""
    P = np.array([tuple(p) for p in pts])
    c = P.mean(axis=0)
    d = Vector(np.linalg.eigh(np.cov((P - c).T))[1][:, -1])  # eigh sorts ascending -> last = longest
    proj = (P - c) @ np.array(d)
    c = Vector(c)
    a, b = c + d * proj.min(), c + d * proj.max()
    if (a - b).length < EPS:
        raise Abort("Selection has no length to span")
    if abs(d.z) >= HORIZONTAL:
        return (a, b) if a.z < b.z else (b, a)
    return (a, b) if (a - origin).length < (b - origin).length else (b, a)


def increment(name):
    """finger.01.L -> finger.02.L, spine1 -> spine2; no digits -> unchanged."""
    m = re.match(r'^(.*?)(\d+)(\D*)$', name)
    if not m:
        return name
    pre, num, post = m.groups()
    return f"{pre}{int(num) + 1:0{len(num)}d}{post}"


def move(eb, head=None, tail=None):
    """Set eb's head/tail; a connected parent's tail and connected children's heads follow."""
    h = (eb.head if head is None else head).copy()
    t = (eb.tail if tail is None else tail).copy()
    p = eb.parent if eb.use_connect else None
    kids = [k for k in eb.children if k.use_connect]
    if ((h - t).length < EPS or (p and (p.head - h).length < EPS)
            or any((k.tail - t).length < EPS for k in kids)):
        raise Abort("A bone would collapse to zero length")
    eb.head, eb.tail = h, t
    if p:
        p.tail = h
    for k in kids:
        k.head = t


def find_bone(ebs, name):
    """Edit bone `name`, None for ''; call before creating anything so Abort leaves no stray bone."""
    p = ebs.get(name) if name else None
    if name and p is None:
        raise Abort(f"No bone named '{name}'")
    return p


def enter_edit(context, objs, active):
    """From any mode, end up in edit mode of objs with `active` active."""
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for ob in context.selected_objects:
        ob.select_set(False)
    for ob in objs:
        ob.select_set(True)
    context.view_layer.objects.active = active
    bpy.ops.object.mode_set(mode='EDIT')


def edit_armature(context, arm, fn, roll='DEFAULT'):
    """Hop into the armature's edit mode, run fn(edit_bones), hop back to mesh edit."""
    meshes = [ob for ob in context.objects_in_mode if ob.type == 'MESH']
    active = context.view_layer.objects.active
    arm_was_selected = arm.select_get()

    enter_edit(context, [arm], arm)
    try:
        bone = fn(arm.data.edit_bones)
        if bone and roll != 'DEFAULT':
            bpy.ops.armature.select_all(action='DESELECT')
            bone.select = bone.select_head = bone.select_tail = True
            bpy.ops.armature.calculate_roll(type=roll)
    finally:
        enter_edit(context, meshes, active)
        arm.select_set(arm_was_selected)


_roll_items = []


def roll_items(self, context):
    """Blender's own calculate_roll modes, plus DEFAULT = leave roll alone."""
    if not _roll_items:
        _roll_items.append(('DEFAULT', "Default", "Leave roll as Blender sets it"))
        _roll_items.extend(
            (i.identifier, i.name, i.description)
            for i in bpy.ops.armature.calculate_roll.get_rna_type().properties['type'].enum_items)
    return _roll_items


def target_armature(context):
    arm = context.scene.easybones_armature
    return arm if arm and arm.type == 'ARMATURE' else None


def active_name(context):
    b = target_armature(context).data.bones.active
    return b.name if b else "Bone"


class _Base:
    bl_options = {'REGISTER', 'UNDO'}

    roll: bpy.props.EnumProperty(name="Roll", items=roll_items, default=0)

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and target_armature(context) is not None

    def invoke(self, context, event):
        """Prefill from the panel; the redo panel then edits these per-op."""
        s = context.scene
        self.roll = s.easybones_roll
        if 'parent' in self.properties.rna_type.properties:
            self.parent = s.easybones_parent
        if 'name' in self.properties.rna_type.properties:
            self.name = self.suggest_name(context)
        return self.execute(context)

    def suggest_name(self, context):
        return active_name(context)

    def draw(self, context):
        """Redo panel: bone-name fields get a search dropdown."""
        data = target_armature(context).data
        for p in self.redo_props(context):
            if p == 'parent':
                self.layout.prop_search(self, p, data, "bones")
            else:
                self.layout.prop(self, p)

    def redo_props(self, context):
        return [p for p in self.properties.rna_type.properties.keys() if p != 'rna_type']

    def prepare(self, context, need_active=True, need_points=True):
        """Returns (armature, world->armature matrix, selected points) or None after reporting."""
        # redo panel re-runs us right after an undo, before the depsgraph refreshes matrix_world
        context.view_layer.update()
        arm = target_armature(context)
        if not arm.visible_get():
            self.report({'ERROR'}, f"Armature '{arm.name}' is hidden")
            return None
        if need_active and arm.data.bones.active is None:
            self.report({'ERROR'}, "No active bone")
            return None
        pts = selected_points(context) if need_points else None
        if need_points and not pts:
            self.report({'ERROR'}, "No vertices selected")
            return None
        return arm, arm.matrix_world.inverted(), pts

    def run(self, context, arm, fn):
        try:
            edit_armature(context, arm, fn, self.roll)
        except Abort as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class MESH_OT_easybones_new(_Base, bpy.types.Operator):
    """New bone with its head at the selection center"""
    bl_idname = "mesh.easybones_new"  # mesh. prefix lets right-click Assign Shortcut find the Mesh keymap
    bl_label = "EasyBones New"

    name: bpy.props.StringProperty(name="Name", default="Bone")
    parent: bpy.props.StringProperty(name="Parent")

    def execute(self, context):
        prep = self.prepare(context, need_active=False)
        if not prep:
            return {'CANCELLED'}
        arm, inv, pts = prep
        c = bbox_center(pts)

        def make(ebs):
            p = find_bone(ebs, self.parent)
            b = ebs.new(self.name)
            b.head = inv @ c
            b.tail = inv @ (c + TEMP_TAIL)
            b.parent = p
            ebs.active = b
            return b

        return self.run(context, arm, make)


class MESH_OT_easybones_head(_Base, bpy.types.Operator):
    """Move the active bone's head to the selection center (a connected parent's tail follows)"""
    bl_idname = "mesh.easybones_head"
    bl_label = "EasyBones Head"

    def execute(self, context):
        prep = self.prepare(context)
        if not prep:
            return {'CANCELLED'}
        arm, inv, pts = prep
        c = bbox_center(pts)

        def fn(ebs):
            move(ebs.active, head=inv @ c)
            return ebs.active

        return self.run(context, arm, fn)


class MESH_OT_easybones_tail(_Base, bpy.types.Operator):
    """Move the active bone's tail to the selection center (connected children follow)"""
    bl_idname = "mesh.easybones_tail"
    bl_label = "EasyBones Tail"

    def execute(self, context):
        prep = self.prepare(context)
        if not prep:
            return {'CANCELLED'}
        arm, inv, pts = prep
        c = bbox_center(pts)

        def fn(ebs):
            move(ebs.active, tail=inv @ c)
            return ebs.active

        return self.run(context, arm, fn)


class MESH_OT_easybones_extend(_Base, bpy.types.Operator):
    """New bone connected to the active bone's tail, its tail at the selection center"""
    bl_idname = "mesh.easybones_extend"
    bl_label = "EasyBones Extend"

    name: bpy.props.StringProperty(name="Name", default="Bone")

    def suggest_name(self, context):
        return increment(active_name(context))

    def execute(self, context):
        prep = self.prepare(context)
        if not prep:
            return {'CANCELLED'}
        arm, inv, pts = prep
        c = bbox_center(pts)

        def make(ebs):
            parent = ebs.active
            if (parent.tail - inv @ c).length < EPS:
                raise Abort("Selection is at the active bone's tail")
            b = ebs.new(self.name)
            b.head = parent.tail
            b.tail = inv @ c
            b.parent = parent
            b.use_connect = True
            ebs.active = b
            return b

        return self.run(context, arm, make)


class MESH_OT_easybones_span(_Base, bpy.types.Operator):
    """Bone along the long axis of the selection, end to end (e.g. a cylinder)"""
    bl_idname = "mesh.easybones_span"
    bl_label = "EasyBones Span"

    target: bpy.props.EnumProperty(name="Target", items=[
        ('NEW', "New", "Create a new bone"),
        ('ACTIVE', "Active", "Refit the active bone (connected neighbors follow)"),
    ])
    invert: bpy.props.BoolProperty(name="Invert", description="Swap head and tail")
    name: bpy.props.StringProperty(name="Name", default="Bone")
    parent: bpy.props.StringProperty(name="Parent")

    def redo_props(self, context):
        if self.target == 'ACTIVE':
            return ['target', 'invert', 'roll']
        return ['target', 'invert', 'name', 'parent', 'roll']

    def execute(self, context):
        prep = self.prepare(context, need_active=self.target == 'ACTIVE')
        if not prep:
            return {'CANCELLED'}
        arm, inv, pts = prep

        def fn(ebs):
            h, t = span(pts, arm.matrix_world.translation)
            if self.invert:
                h, t = t, h
            h, t = inv @ h, inv @ t
            if self.target == 'ACTIVE':
                move(ebs.active, head=h, tail=t)
                return ebs.active
            p = find_bone(ebs, self.parent)
            b = ebs.new(self.name)
            b.head, b.tail = h, t
            b.parent = p
            ebs.active = b
            return b

        return self.run(context, arm, fn)


class MESH_OT_easybones_parent(_Base, bpy.types.Operator):
    """Parent the active bone (keep offset). Pick: choose the parent, also used for new bones"""
    bl_idname = "mesh.easybones_parent"
    bl_label = "EasyBones Parent"

    parent: bpy.props.StringProperty(name="Parent")
    pick: bpy.props.BoolProperty(name="Pick", options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        self.parent = context.scene.easybones_parent
        if self.pick:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def redo_props(self, context):
        return ['parent']

    def execute(self, context):
        prep = self.prepare(context, need_points=False)
        if not prep:
            return {'CANCELLED'}
        arm = prep[0]
        if self.pick:
            context.scene.easybones_parent = self.parent

        def fn(ebs):
            eb, p = ebs.active, find_bone(ebs, self.parent)
            if p and (p == eb or eb in p.parent_recursive):
                raise Abort(f"'{p.name}' is '{eb.name}' or its child")
            eb.use_connect = False  # keep offset
            eb.parent = p

        return self.run(context, arm, fn)


class MESH_OT_easybones_flip(bpy.types.Operator):
    """Flip between mesh edit and the target armature's edit mode"""
    bl_idname = "mesh.easybones_flip"
    bl_label = "EasyBones Flip"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode == 'EDIT_MESH':
            return target_armature(context) is not None
        return context.mode == 'EDIT_ARMATURE'

    def execute(self, context):
        s = context.scene
        if context.mode == 'EDIT_MESH':
            arm = target_armature(context)
            if not arm.visible_get():
                self.report({'ERROR'}, f"Armature '{arm.name}' is hidden")
                return {'CANCELLED'}
            meshes = [ob for ob in context.objects_in_mode if ob.type == 'MESH']
            active = context.view_layer.objects.active
            # active first; names so it survives save/reload
            s.easybones_flip = "\n".join([active.name] + [ob.name for ob in meshes if ob != active])
            enter_edit(context, [arm], arm)
            return {'FINISHED'}
        meshes = [ob for n in s.easybones_flip.split("\n")
                  if (ob := s.objects.get(n)) and ob.type == 'MESH' and ob.visible_get()]
        if not meshes:
            self.report({'ERROR'}, "No mesh to return to")
            return {'CANCELLED'}
        enter_edit(context, meshes, meshes[0])
        return {'FINISHED'}


class EASYBONES_MT_pie(bpy.types.Menu):
    bl_label = "EasyBones"

    def draw(self, context):
        pie = self.layout.menu_pie()
        # order: W, E, S, N, NW, NE, SW, SE
        pie.operator("mesh.easybones_new", text="New", icon='BONE_DATA')
        pie.operator("mesh.easybones_extend", text="Extend", icon='CON_FOLLOWPATH')
        pie.operator("mesh.easybones_flip", text="Flip to Armature", icon='ARMATURE_DATA')
        pie.operator("mesh.easybones_span", text="Span", icon='DRIVER_DISTANCE')
        pie.operator("mesh.easybones_parent", text="Parent…", icon='LINKED').pick = True
        pie.separator()
        pie.operator("mesh.easybones_head", text="Head", icon='TRIA_DOWN')
        pie.operator("mesh.easybones_tail", text="Tail", icon='TRIA_UP')


class EASYBONES_PT_panel(bpy.types.Panel):
    bl_label = "EasyBones"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Rigging"  # overwritten from prefs on register
    bl_context = "mesh_edit"

    def draw(self, context):
        s = context.scene
        col = self.layout.column()
        col.prop(s, "easybones_armature", text="")
        col.prop(s, "easybones_roll")
        arm = target_armature(context)
        if arm:
            col.prop_search(arm.data.bones, "active", arm.data, "bones", text="Active")
            row = col.row(align=True)
            row.prop_search(s, "easybones_parent", arm.data, "bones", text="Parent")
            row.operator("mesh.easybones_parent", text="", icon='LINKED')
        row = col.row(align=True)
        row.operator("mesh.easybones_new", text="New")
        row.operator("mesh.easybones_extend", text="Extend")
        row.operator("mesh.easybones_span", text="Span")
        row = col.row(align=True)
        row.operator("mesh.easybones_head", text="Head")
        row.operator("mesh.easybones_tail", text="Tail")
        col.operator("mesh.easybones_flip", text="Flip to Armature")


def update_category(self, context):
    bpy.utils.unregister_class(EASYBONES_PT_panel)
    EASYBONES_PT_panel.bl_category = self.panel_category
    bpy.utils.register_class(EASYBONES_PT_panel)


class EASYBONES_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    panel_category: bpy.props.StringProperty(
        name="Panel Category", default="Rigging", update=update_category)

    def draw(self, context):
        col = self.layout.box().column()
        col.label(text="Set the category to show EasyBones panel:")
        col.prop(self, "panel_category")


classes = (MESH_OT_easybones_new, MESH_OT_easybones_head, MESH_OT_easybones_tail,
           MESH_OT_easybones_extend, MESH_OT_easybones_span, MESH_OT_easybones_parent,
           MESH_OT_easybones_flip, EASYBONES_MT_pie)
OPS = ("mesh.easybones_new", "mesh.easybones_head", "mesh.easybones_tail",
       "mesh.easybones_extend", "mesh.easybones_span", "mesh.easybones_parent")
addon_keymaps = []


def register():
    S = bpy.types.Scene
    S.easybones_armature = bpy.props.PointerProperty(
        name="Armature", type=bpy.types.Object, poll=lambda self, ob: ob.type == 'ARMATURE')
    S.easybones_roll = bpy.props.EnumProperty(name="Roll", items=roll_items, default=0)
    S.easybones_parent = bpy.props.StringProperty(
        name="Parent", description="Parent for new bones (empty = none)")
    S.easybones_flip = bpy.props.StringProperty(options={'HIDDEN'})  # meshes Flip returns to
    for c in classes:
        bpy.utils.register_class(c)
    bpy.utils.register_class(EASYBONES_Prefs)
    addon = bpy.context.preferences.addons.get(__package__)
    if addon:
        EASYBONES_PT_panel.bl_category = addon.preferences.panel_category
    bpy.utils.register_class(EASYBONES_PT_panel)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Mesh")
        kmi = km.keymap_items.new("wm.call_menu_pie", 'B', 'PRESS', shift=True, alt=True)
        kmi.properties.name = "EASYBONES_MT_pie"
        addon_keymaps.append((km, kmi))
        # Unbound entries so they show in Preferences > Keymap > Mesh, ready to assign
        for op in OPS:
            addon_keymaps.append((km, km.keymap_items.new(op, 'NONE', 'PRESS')))
        km = kc.keymaps.new(name="Armature")
        addon_keymaps.append((km, km.keymap_items.new(
            "mesh.easybones_flip", 'B', 'PRESS', shift=True, alt=True)))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    bpy.utils.unregister_class(EASYBONES_PT_panel)
    bpy.utils.unregister_class(EASYBONES_Prefs)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.easybones_armature
    del bpy.types.Scene.easybones_roll
    del bpy.types.Scene.easybones_parent
    del bpy.types.Scene.easybones_flip
