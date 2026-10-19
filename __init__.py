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


class EASYBONES_OT_new(_Base, bpy.types.Operator):
    """New orphan bone with its head at the selection center"""
    bl_idname = "easybones.new"
    bl_label = "New"

    name: bpy.props.StringProperty(name="Name", default="Bone")

    def invoke(self, context, event):
        self.name = context.scene.easybones_last_name
        return context.window_manager.invoke_props_dialog(self)

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
        context.scene.easybones_last_name = self.name
        return {'FINISHED'}


class EASYBONES_OT_tail(_Base, bpy.types.Operator):
    """Move the active bone's tail to the selection center"""
    bl_idname = "easybones.tail"
    bl_label = "Tail"

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


class EASYBONES_OT_extend(_Base, bpy.types.Operator):
    """New bone connected to the active bone's tail, its tail at the selection center"""
    bl_idname = "easybones.extend"
    bl_label = "Extend"

    name: bpy.props.StringProperty(name="Name", default="Bone")

    def invoke(self, context, event):
        self.name = increment(context.scene.easybones_last_name)
        return context.window_manager.invoke_props_dialog(self)

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
        context.scene.easybones_last_name = self.name
        return {'FINISHED'}


class EASYBONES_PT_panel(bpy.types.Panel):
    bl_label = "EasyBones"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "EasyBones"
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
        row.operator("easybones.new")
        row.operator("easybones.tail")
        row.operator("easybones.extend")


classes = (EASYBONES_OT_new, EASYBONES_OT_tail, EASYBONES_OT_extend, EASYBONES_PT_panel)


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
    S.easybones_last_name = bpy.props.StringProperty(default="Bone")
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.easybones_armature
    del bpy.types.Scene.easybones_roll
    del bpy.types.Scene.easybones_last_name
