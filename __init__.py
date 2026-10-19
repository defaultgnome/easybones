import re

import bmesh
import bpy
from mathutils import Vector

TEMP_TAIL = Vector((0, 0, 0.1))  # world-space offset for a fresh bone's tail


def selection_center(context):
    """World-space bounding-box center of selected verts across all meshes in edit mode."""
    pts = []
    for ob in context.objects_in_mode:
        if ob.type != 'MESH':
            continue
        mw = ob.matrix_world
        pts += [mw @ v.co for v in bmesh.from_edit_mesh(ob.data).verts if v.select]
    if not pts:
        return None
    lo = Vector(min(p[i] for p in pts) for i in range(3))
    hi = Vector(max(p[i] for p in pts) for i in range(3))
    return (lo + hi) / 2


def increment(name):
    """finger.01.L -> finger.02.L, spine1 -> spine2; no digits -> unchanged."""
    m = re.match(r'^(.*?)(\d+)(\D*)$', name)
    if not m:
        return name
    pre, num, post = m.groups()
    return f"{pre}{int(num) + 1:0{len(num)}d}{post}"


def edit_armature(context, arm, fn):
    """Hop into the armature's edit mode, run fn(edit_bones), hop back to mesh edit."""
    meshes = [ob for ob in context.objects_in_mode if ob.type == 'MESH']
    active = context.view_layer.objects.active
    arm_was_selected = arm.select_get()

    bpy.ops.object.mode_set(mode='OBJECT')
    for ob in context.selected_objects:
        ob.select_set(False)
    arm.select_set(True)
    context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bone = fn(arm.data.edit_bones)
        roll = context.scene.easybones_roll
        if roll != 'DEFAULT':
            bpy.ops.armature.select_all(action='DESELECT')
            bone.select = bone.select_head = bone.select_tail = True
            bpy.ops.armature.calculate_roll(type=roll)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        arm.select_set(arm_was_selected)
        for ob in meshes:
            ob.select_set(True)
        context.view_layer.objects.active = active
        bpy.ops.object.mode_set(mode='EDIT')


class _Base:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = context.scene.easybones_armature
        return context.mode == 'EDIT_MESH' and arm is not None and arm.type == 'ARMATURE'

    def prepare(self, context, need_active):
        """Returns (armature, world->armature matrix, center) or None after reporting."""
        arm = context.scene.easybones_armature
        if not arm.visible_get():
            self.report({'ERROR'}, f"Armature '{arm.name}' is hidden")
            return None
        if need_active and arm.data.bones.active is None:
            self.report({'ERROR'}, "No active bone")
            return None
        c = selection_center(context)
        if c is None:
            self.report({'ERROR'}, "No vertices selected")
            return None
        return arm, arm.matrix_world.inverted(), c


def invoke_then_rename(op, context):
    """Run op, then pop up an F2-style name field on the new bone."""
    ret = op.execute(context)
    if 'FINISHED' in ret:
        bpy.ops.wm.call_panel(name="EASYBONES_PT_rename", keep_open=False)
    return ret


def active_name(context):
    arm = context.scene.easybones_armature
    b = arm.data.bones.active
    return b.name if b else "Bone"


class MESH_OT_easybones_new(_Base, bpy.types.Operator):
    """New orphan bone with its head at the selection center"""
    bl_idname = "mesh.easybones_new"  # mesh. prefix lets right-click Assign Shortcut find the Mesh keymap
    bl_label = "EasyBones New"

    name: bpy.props.StringProperty(name="Name", default="Bone")

    def invoke(self, context, event):
        self.name = active_name(context)
        return invoke_then_rename(self, context)

    def execute(self, context):
        prep = self.prepare(context, need_active=False)
        if not prep:
            return {'CANCELLED'}
        arm, inv, c = prep

        def make(ebs):
            b = ebs.new(self.name)
            b.head = inv @ c
            b.tail = inv @ (c + TEMP_TAIL)
            ebs.active = b
            return b

        edit_armature(context, arm, make)
        return {'FINISHED'}


class MESH_OT_easybones_tail(_Base, bpy.types.Operator):
    """Move the active bone's tail to the selection center"""
    bl_idname = "mesh.easybones_tail"
    bl_label = "EasyBones Tail"

    def execute(self, context):
        prep = self.prepare(context, need_active=True)
        if not prep:
            return {'CANCELLED'}
        arm, inv, c = prep
        b = arm.data.bones.active
        # zero-length bones get deleted by Blender on leaving edit mode
        if ((arm.matrix_world @ b.head_local) - c).length < 1e-6:
            self.report({'ERROR'}, "Tail would equal head")
            return {'CANCELLED'}

        def move(ebs):
            eb = ebs.active
            eb.tail = inv @ c
            return eb

        edit_armature(context, arm, move)
        return {'FINISHED'}


class MESH_OT_easybones_extend(_Base, bpy.types.Operator):
    """New bone connected to the active bone's tail, its tail at the selection center"""
    bl_idname = "mesh.easybones_extend"
    bl_label = "EasyBones Extend"

    name: bpy.props.StringProperty(name="Name", default="Bone")

    def invoke(self, context, event):
        self.name = increment(active_name(context))
        return invoke_then_rename(self, context)

    def execute(self, context):
        prep = self.prepare(context, need_active=True)
        if not prep:
            return {'CANCELLED'}
        arm, inv, c = prep
        if ((arm.matrix_world @ arm.data.bones.active.tail_local) - c).length < 1e-6:
            self.report({'ERROR'}, "Selection is at the active bone's tail")
            return {'CANCELLED'}

        def make(ebs):
            parent = ebs.active
            b = ebs.new(self.name)
            b.head = parent.tail
            b.tail = inv @ c
            b.parent = parent
            b.use_connect = True
            ebs.active = b
            return b

        edit_armature(context, arm, make)
        return {'FINISHED'}


class EASYBONES_PT_rename(bpy.types.Panel):
    """Popup only (wm.call_panel), mirrors Blender's F2 TOPBAR_PT_name"""
    bl_label = "Bone Name"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'

    def draw(self, context):
        b = context.scene.easybones_armature.data.bones.active
        row = self.layout.row()
        row.activate_init = True  # focus field with text selected, Enter confirms
        row.prop(b, "name", text="", icon='BONE_DATA')


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
        arm = s.easybones_armature
        active = arm.data.bones.active if arm and arm.type == 'ARMATURE' else None
        col.label(text=f"Active: {active.name if active else '-'}", icon='BONE_DATA')
        row = col.row(align=True)
        row.operator("mesh.easybones_new", text="New")
        row.operator("mesh.easybones_tail", text="Tail")
        row.operator("mesh.easybones_extend", text="Extend")


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


classes = (MESH_OT_easybones_new, MESH_OT_easybones_tail, MESH_OT_easybones_extend,
           EASYBONES_PT_rename)
OPS = ("mesh.easybones_new", "mesh.easybones_tail", "mesh.easybones_extend")
addon_keymaps = []


def register():
    # Roll modes mirror Blender's own calculate_roll, plus DEFAULT = leave roll alone
    roll_items = [('DEFAULT', "Default", "Leave roll as Blender sets it")] + [
        (i.identifier, i.name, i.description)
        for i in bpy.ops.armature.calculate_roll.get_rna_type().properties['type'].enum_items
    ]
    S = bpy.types.Scene
    S.easybones_armature = bpy.props.PointerProperty(
        name="Armature", type=bpy.types.Object, poll=lambda self, ob: ob.type == 'ARMATURE')
    S.easybones_roll = bpy.props.EnumProperty(name="Roll", items=roll_items, default='DEFAULT')
    for c in classes:
        bpy.utils.register_class(c)
    bpy.utils.register_class(EASYBONES_Prefs)
    addon = bpy.context.preferences.addons.get(__package__)
    if addon:
        EASYBONES_PT_panel.bl_category = addon.preferences.panel_category
    bpy.utils.register_class(EASYBONES_PT_panel)

    # Unbound entries so they show in Preferences > Keymap > Mesh, ready to assign
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Mesh")
        for op in OPS:
            addon_keymaps.append((km, km.keymap_items.new(op, 'NONE', 'PRESS')))


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
