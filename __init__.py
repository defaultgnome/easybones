import math
import re
from contextlib import contextmanager

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


def edit_armature(context, arm, fn):
    """Hop into the armature's edit mode, run fn(edit_bones), hop back to mesh edit."""
    meshes = [ob for ob in context.objects_in_mode if ob.type == 'MESH']
    active = context.view_layer.objects.active
    arm_was_selected = arm.select_get()

    enter_edit(context, [arm], arm)
    try:
        fn(arm.data.edit_bones)
    finally:
        enter_edit(context, meshes, active)
        arm.select_set(arm_was_selected)


def target_armature(context):
    arm = context.scene.easybones_armature
    return arm if arm and arm.type == 'ARMATURE' else None


def active_name(context):
    b = target_armature(context).data.bones.active
    return b.name if b else "Bone"


def weight_armature(context):
    """Armature that deforms the active mesh (its Armature modifier), else the target armature."""
    ob = context.object
    for m in (ob.modifiers if ob else ()):
        if m.type == 'ARMATURE' and m.object:
            return m.object
    return target_armature(context)


def bone_context(context):
    """(armature, bones to browse) for the current mode, or None.
    Mesh edit and weight paint browse the armature's bones; armature edit its edit bones."""
    if context.mode == 'EDIT_MESH':
        arm = target_armature(context)
    elif context.mode == 'PAINT_WEIGHT':
        arm = weight_armature(context)
    elif context.mode in {'EDIT_ARMATURE', 'POSE'}:
        arm = context.object if context.object and context.object.type == 'ARMATURE' else None
    else:
        return None
    if arm is None:
        return None
    return arm, arm.data.edit_bones if context.mode == 'EDIT_ARMATURE' else arm.data.bones


def set_selected(arm, bone, state, edit):
    """Select a bone. Edit bones carry the flags; otherwise the pose bone does (newer Blender
    dropped them from Bone), falling back to the Bone for older versions."""
    if edit:
        bone.select = bone.select_head = bone.select_tail = state
        return
    pb = arm.pose.bones.get(bone.name)
    target = pb if pb is not None and hasattr(pb, 'select') else bone
    if hasattr(target, 'select'):
        target.select = state


@contextmanager
def armature_mode(context, mode):
    """Run the body with the active armature in 'EDIT' or 'POSE' mode, then restore the mode we were in."""
    back = {'EDIT_ARMATURE': 'EDIT', 'POSE': 'POSE'}[context.mode]
    if back != mode:
        bpy.ops.object.mode_set(mode=mode)
    try:
        yield
    finally:
        if back != mode:
            bpy.ops.object.mode_set(mode=back)


def is_selected(pose_bone):
    target = pose_bone if hasattr(pose_bone, 'select') else pose_bone.bone
    return getattr(target, 'select', False)



def chain_step(names, name, step):
    """Name `step` places along name's numbered series (same text around the last number),
    wrapping at the ends: finger.01.L -> finger.02.L. None if no number or no other bone."""
    m = re.match(r'^(.*?)(\d+)(\D*)$', name)
    if not m:
        return None
    pre, _, post = m.groups()
    rx = re.compile(rf"^{re.escape(pre)}(\d+){re.escape(post)}$")
    series = sorted((int(g.group(1)), n) for n in names if (g := rx.match(n)))
    if len(series) < 2:
        return None
    ordered = [n for _, n in series]
    return ordered[(ordered.index(name) + step) % len(ordered)]


class _Base:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and target_armature(context) is not None

    def invoke(self, context, event):
        """Prefill from the panel; the redo panel then edits these per-op."""
        if 'name' in self.properties.rna_type.properties:
            self.name = self.suggest_name(context)
        return self.execute(context)

    def suggest_name(self, context):
        return active_name(context)

    def draw(self, context):
        for p in self.redo_props(context):
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
            edit_armature(context, arm, fn)
        except Abort as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class MESH_OT_easybones_new(_Base, bpy.types.Operator):
    """New bone with its head at the selection center"""
    bl_idname = "mesh.easybones_new"  # mesh. prefix lets right-click Assign Shortcut find the Mesh keymap
    bl_label = "EasyBones New"

    name: bpy.props.StringProperty(name="Name", default="Bone", description="Name for the new bone")

    def execute(self, context):
        prep = self.prepare(context, need_active=False)
        if not prep:
            return {'CANCELLED'}
        arm, inv, pts = prep
        c = bbox_center(pts)

        def make(ebs):
            b = ebs.new(self.name)
            b.head = inv @ c
            b.tail = inv @ (c + TEMP_TAIL)
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


class MESH_OT_easybones_move(_Base, bpy.types.Operator):
    """Move the whole active bone so its head lands on the selection center, keeping its length and direction"""
    bl_idname = "mesh.easybones_move"
    bl_label = "EasyBones Move"

    def execute(self, context):
        prep = self.prepare(context)
        if not prep:
            return {'CANCELLED'}
        arm, inv, pts = prep
        c = bbox_center(pts)

        def fn(ebs):
            eb = ebs.active
            h = inv @ c
            move(eb, head=h, tail=h + (eb.tail - eb.head))
            return eb

        return self.run(context, arm, fn)


class MESH_OT_easybones_extend(_Base, bpy.types.Operator):
    """New bone connected to the active bone's tail, its tail at the selection center"""
    bl_idname = "mesh.easybones_extend"
    bl_label = "EasyBones Extend"

    name: bpy.props.StringProperty(name="Name", default="Bone", description="Name for the new bone")

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
    name: bpy.props.StringProperty(name="Name", default="Bone", description="Name for the new bone")

    def redo_props(self, context):
        if self.target == 'ACTIVE':
            return ['target', 'invert']
        return ['target', 'invert', 'name']

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
            b = ebs.new(self.name)
            b.head, b.tail = h, t
            ebs.active = b
            return b

        return self.run(context, arm, fn)


class MESH_OT_easybones_flip(bpy.types.Operator):
    """Flip between mesh edit and the target armature's edit mode (from pose mode, back to the meshes)"""
    bl_idname = "mesh.easybones_flip"
    bl_label = "EasyBones Flip"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode == 'EDIT_MESH':
            return target_armature(context) is not None
        return context.mode in {'EDIT_ARMATURE', 'POSE'}

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
            self.report({'ERROR'}, "No mesh to return to. First select a mesh and flip from there")
            return {'CANCELLED'}
        enter_edit(context, meshes, meshes[0])
        return {'FINISHED'}


class _Select:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bone_context(context) is not None

    def pick(self, context, arm, bones, bone):
        """Make bone the active (and, outside mesh edit, only selected) bone."""
        if context.mode != 'EDIT_MESH':
            if bone.hide:
                self.report({'WARNING'}, f"Bone '{bone.name}' is hidden")
                return {'CANCELLED'}
            edit = context.mode == 'EDIT_ARMATURE'
            for b in bones:
                set_selected(arm, b, False, edit)
            set_selected(arm, bone, True, edit)
        bones.active = bone
        if context.mode == 'PAINT_WEIGHT':  # like Ctrl+Shift+click: paint this bone's vertex group
            ob = context.object
            vg = ob.vertex_groups.get(bone.name)
            if vg:
                ob.vertex_groups.active_index = vg.index
            else:
                self.report({'INFO'}, f"'{bone.name}' has no vertex group on {ob.name}")
        return {'FINISHED'}

    def step(self, context, step):
        arm, bones = bone_context(context)
        active = bones.active
        if active is None:
            self.report({'WARNING'}, "No active bone")
            return {'CANCELLED'}
        if context.scene.easybones_follow == 'PARENTING':
            if step > 0:
                other = active.children[0] if active.children else None
            else:
                other = active.parent
            if other is None:
                self.report({'WARNING'}, f"'{active.name}' has no {'child' if step > 0 else 'parent'}")
                return {'CANCELLED'}
            return self.pick(context, arm, bones, other)
        name = chain_step([b.name for b in bones], active.name, step)
        if name is None:
            self.report({'WARNING'}, f"'{active.name}' has no numbered chain to step through")
            return {'CANCELLED'}
        return self.pick(context, arm, bones, bones[name])


class ARMATURE_OT_easybones_select_mirror(_Select, bpy.types.Operator):
    """Make the active bone's mirror (.L <-> .R) the active bone"""
    bl_idname = "armature.easybones_select_mirror"
    bl_label = "EasyBones Select Mirror"

    def execute(self, context):
        arm, bones = bone_context(context)
        active = bones.active
        if active is None:
            self.report({'WARNING'}, "No active bone")
            return {'CANCELLED'}
        flipped = bpy.utils.flip_name(active.name)
        other = bones.get(flipped) if flipped != active.name else None
        if other is None:
            self.report({'WARNING'}, f"No mirror bone for '{active.name}'")
            return {'CANCELLED'}
        return self.pick(context, arm, bones, other)


class ARMATURE_OT_easybones_select_next(_Select, bpy.types.Operator):
    """Make the next bone the active bone. Follow Naming: next number in the name (spine.01 -> spine.02, the last wraps to the first). Follow Parenting: the first child"""
    bl_idname = "armature.easybones_select_next"
    bl_label = "EasyBones Select Next"

    def execute(self, context):
        return self.step(context, 1)


class ARMATURE_OT_easybones_select_prev(_Select, bpy.types.Operator):
    """Make the previous bone the active bone. Follow Naming: previous number in the name (spine.02 -> spine.01, the first wraps to the last). Follow Parenting: the parent"""
    bl_idname = "armature.easybones_select_prev"
    bl_label = "EasyBones Select Previous"

    def execute(self, context):
        return self.step(context, -1)


class _Prefixes:
    """Shared by the DEF -> ORG style ops; fields prefill from the panel, the redo panel edits them."""
    bl_options = {'REGISTER', 'UNDO'}

    from_prefix: bpy.props.StringProperty(name="From", description="Name prefix of the bones to take")
    to_prefix: bpy.props.StringProperty(name="To", description="Name prefix that replaces it on the counterpart bones")
    selected_only: bpy.props.BoolProperty(name="Selected Only", description="Only consider selected bones")

    @classmethod
    def poll(cls, context):
        return context.mode in {'EDIT_ARMATURE', 'POSE'} and context.object and context.object.type == 'ARMATURE'

    def invoke(self, context, event):
        s = context.scene
        self.from_prefix, self.to_prefix = s.easybones_from, s.easybones_to
        self.selected_only = s.easybones_selected_only
        return self.execute(context)

    def check_prefixes(self):
        if not self.from_prefix or self.from_prefix == self.to_prefix:
            self.report({'ERROR'}, "Set different From and To prefixes")
            return False
        return True

    def renamed(self, name):
        """name with its From prefix swapped for To, or None if it doesn't start with From."""
        return self.to_prefix + name[len(self.from_prefix):] if name.startswith(self.from_prefix) else None


class ARMATURE_OT_easybones_copy_to_layer(_Prefixes, bpy.types.Operator):
    """Copy the From* bones to To* (same position, hierarchy kept, non-deforming) into a bone collection named To. Does nothing if any new name already exists. Works from armature edit or pose mode"""
    bl_idname = "armature.easybones_copy_to_layer"
    bl_label = "EasyBones Clone Layer"

    def execute(self, context):
        if not self.check_prefixes():
            return {'CANCELLED'}
        arm = context.object
        with armature_mode(context, 'EDIT'):
            return self.clone(arm)

    def clone(self, arm):
        ebs = arm.data.edit_bones
        src = [b for b in ebs if (not self.selected_only or b.select) and self.renamed(b.name)]
        if not src:
            self.report({'ERROR'}, f"No bones start with '{self.from_prefix}'")
            return {'CANCELLED'}
        taken = [self.renamed(b.name) for b in src if self.renamed(b.name) in ebs]
        if taken:
            self.report({'ERROR'}, f"Name conflict, nothing copied: {', '.join(taken[:3])}"
                        + (" ..." if len(taken) > 3 else ""))
            return {'CANCELLED'}

        coll = arm.data.collections_all.get(self.to_prefix) or arm.data.collections.new(self.to_prefix)
        copies = {}
        for b in src:
            c = ebs.new(self.renamed(b.name))
            c.head, c.tail, c.roll = b.head, b.tail, b.roll
            c.use_deform = False
            copies[b.name] = c
        for b in src:
            c = copies[b.name]
            c.parent = copies.get(b.parent.name, b.parent) if b.parent else None
            c.use_connect = b.use_connect and c.parent is not None
            for old in list(c.collections):
                old.unassign(c)
            coll.assign(c)
        self.report({'INFO'}, f"Cloned {len(src)} bones to '{coll.name}'")
        return {'FINISHED'}


class POSE_OT_easybones_link_transforms(_Prefixes, bpy.types.Operator):
    """Add a Copy Transforms constraint to each From* bone, targeting its To* counterpart. Bones already linked are skipped. Works from armature edit or pose mode"""
    bl_idname = "pose.easybones_link_transforms"
    bl_label = "EasyBones Copy Transform"

    def execute(self, context):
        if not self.check_prefixes():
            return {'CANCELLED'}
        arm = context.object
        with armature_mode(context, 'POSE'):
            return self.link(arm)

    def link(self, arm):
        pbs = arm.pose.bones
        linked, missing = 0, []
        for pb in [b for b in pbs if self.renamed(b.name)]:
            if self.selected_only and not is_selected(pb):
                continue
            target = self.renamed(pb.name)
            if target not in pbs:
                missing.append(target)
            elif not any(c.type == 'COPY_TRANSFORMS' and c.target == arm and c.subtarget == target
                         for c in pb.constraints):
                con = pb.constraints.new('COPY_TRANSFORMS')
                con.target, con.subtarget = arm, target
                linked += 1
        for name in missing:
            print(f"EasyBones: {name} not found")
        if missing:
            self.report({'WARNING'}, f"Linked {linked}; {len(missing)} counterparts not found (see console)")
        elif linked:
            self.report({'INFO'}, f"Linked {linked} bones")
        else:
            self.report({'WARNING'}, f"Nothing to link for '{self.from_prefix}'")
        return {'FINISHED'} if linked else {'CANCELLED'}



class EASYBONES_MT_select_pie(bpy.types.Menu):
    bl_label = "EasyBones Select"

    def draw(self, context):
        pie = self.layout.menu_pie()
        pie.operator("armature.easybones_select_prev", text="Previous", icon='TRIA_LEFT')
        pie.operator("armature.easybones_select_next", text="Next", icon='TRIA_RIGHT')
        pie.operator("armature.easybones_select_mirror", text="Mirror", icon='MOD_MIRROR')


class EASYBONES_MT_pie(bpy.types.Menu):
    bl_label = "EasyBones"

    def draw(self, context):
        pie = self.layout.menu_pie()
        # order: W, E, S, N, NW, NE, SW, SE
        pie.operator("mesh.easybones_new", text="New", icon='BONE_DATA')
        pie.operator("mesh.easybones_extend", text="Extend", icon='CON_FOLLOWPATH')
        pie.operator("mesh.easybones_flip", text="Flip to Armature", icon='ARMATURE_DATA')
        pie.operator("mesh.easybones_span", text="Span", icon='DRIVER_DISTANCE')
        pie.operator("mesh.easybones_move", text="Move", icon='EMPTY_ARROWS')
        pie.separator()
        pie.operator("mesh.easybones_head", text="Head", icon='TRIA_DOWN')
        pie.operator("mesh.easybones_tail", text="Tail", icon='TRIA_UP')


class EASYBONES_PT_panel(bpy.types.Panel):
    bl_label = "EasyBones"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Rigging"  # overwritten from prefs on register

    @classmethod
    def poll(cls, context):
        return context.mode in {'EDIT_MESH', 'EDIT_ARMATURE', 'POSE', 'PAINT_WEIGHT'}

    def draw(self, context):
        mode = context.mode
        col = self.layout.column()
        col.separator()
        col.prop(context.scene, "easybones_armature", text="Armature")
        if mode != 'PAINT_WEIGHT':
            col.operator("mesh.easybones_flip", text="Flip to Armature" if mode == 'EDIT_MESH' else "Flip to Mesh",
                         icon='ARROW_LEFTRIGHT')
        ctx = bone_context(context)
        if ctx:
            arm, bones = ctx
            holder, search = (bones, "edit_bones") if mode == 'EDIT_ARMATURE' else (bones, "bones")
            col.prop_search(holder, "active", arm.data, search, text="Active")


class EASYBONES_PT_select(bpy.types.Panel):
    bl_label = "Select Bone"
    bl_parent_id = "EASYBONES_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    @classmethod
    def poll(cls, context):
        return bone_context(context) is not None

    def draw(self, context):
        col = self.layout.column()
        col.prop(context.scene, "easybones_follow", expand=True)
        row = col.row(align=True)
        row.operator("armature.easybones_select_prev", text="Prev", icon='TRIA_LEFT')
        row.operator("armature.easybones_select_next", text="Next", icon='TRIA_RIGHT')
        col.operator("armature.easybones_select_mirror", text="Mirror (.L / .R)", icon='MOD_MIRROR')


class EASYBONES_PT_mesh(bpy.types.Panel):
    bl_label = "Place Bones"
    bl_parent_id = "EASYBONES_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        col = self.layout.column()
        row = col.row(align=True)
        row.operator("mesh.easybones_new", text="New")
        row.operator("mesh.easybones_extend", text="Extend")
        row.operator("mesh.easybones_span", text="Span")
        row = col.row(align=True)
        row.operator("mesh.easybones_head", text="Head")
        row.operator("mesh.easybones_tail", text="Tail")
        row.operator("mesh.easybones_move", text="Move")


class EASYBONES_PT_rig(bpy.types.Panel):
    bl_label = "Rig Layers"
    bl_parent_id = "EASYBONES_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    @classmethod
    def poll(cls, context):
        return context.mode in {'EDIT_ARMATURE', 'POSE'}

    def draw(self, context):
        s = context.scene
        col = self.layout.column()
        row = col.row(align=True)
        row.prop(s, "easybones_from", text="")
        row.label(text="", icon='FORWARD')
        row.prop(s, "easybones_to", text="")
        col.prop(s, "easybones_selected_only")
        col.operator("armature.easybones_copy_to_layer", text="Clone Layer", icon='DUPLICATE')
        col.operator("pose.easybones_link_transforms", text="Copy Transform", icon='CON_TRANSLIKE')


PANELS = (EASYBONES_PT_panel, EASYBONES_PT_select, EASYBONES_PT_mesh, EASYBONES_PT_rig)


def update_category(self, context):
    for p in reversed(PANELS):
        bpy.utils.unregister_class(p)
    EASYBONES_PT_panel.bl_category = self.panel_category
    for p in PANELS:
        bpy.utils.register_class(p)



class EASYBONES_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    panel_category: bpy.props.StringProperty(
        name="Panel Category", default="Rigging", update=update_category,
        description="Sidebar tab the EasyBones panel appears in")

    def draw(self, context):
        col = self.layout.box().column()
        col.label(text="Set the category to show EasyBones panel:")
        col.prop(self, "panel_category")


classes = (MESH_OT_easybones_new, MESH_OT_easybones_head, MESH_OT_easybones_tail,
           MESH_OT_easybones_move, MESH_OT_easybones_extend, MESH_OT_easybones_span,
           MESH_OT_easybones_flip,
           ARMATURE_OT_easybones_select_mirror, ARMATURE_OT_easybones_select_next,
           ARMATURE_OT_easybones_select_prev,
           ARMATURE_OT_easybones_copy_to_layer, POSE_OT_easybones_link_transforms,
           EASYBONES_MT_pie, EASYBONES_MT_select_pie)
SELECT_OPS = ("armature.easybones_select_mirror", "armature.easybones_select_next",
              "armature.easybones_select_prev")
# keymap -> operators listed unbound in Preferences > Keymap, ready to assign
UNBOUND = {
    "Mesh": ("mesh.easybones_new", "mesh.easybones_head", "mesh.easybones_tail",
             "mesh.easybones_move", "mesh.easybones_extend", "mesh.easybones_span") + SELECT_OPS,
    "Armature": SELECT_OPS + ("armature.easybones_copy_to_layer",),
    "Pose": SELECT_OPS + ("pose.easybones_link_transforms",),
    "Weight Paint": SELECT_OPS,
}
addon_keymaps = []


def register():
    S = bpy.types.Scene
    S.easybones_armature = bpy.props.PointerProperty(
        name="Armature", description="Armature that EasyBones creates and edits bones in",
        type=bpy.types.Object, poll=lambda self, ob: ob.type == 'ARMATURE')
    S.easybones_flip = bpy.props.StringProperty(options={'HIDDEN'})  # meshes Flip returns to
    S.easybones_from = bpy.props.StringProperty(
        name="From", default="DEF",
        description="Name prefix of the bones Clone Layer / Copy Transform take (e.g. DEF)")
    S.easybones_to = bpy.props.StringProperty(
        name="To", default="ORG", description="Name prefix of their counterparts, e.g. ORG. Clone Layer also puts the copies in a "
                    "bone collection with this name")
    S.easybones_selected_only = bpy.props.BoolProperty(
        name="Selected Only", description="Clone Layer / Copy Transform only touch the selected bones")
    S.easybones_follow = bpy.props.EnumProperty(
        name="Follow", default='NAMING', items=[
            ('NAMING', "Naming", "Prev/Next step through the number in the bone name (spine.01 -> spine.02) and cycle: the last goes back to the first"),
            ('PARENTING', "Parenting", "Next goes to the first child, Prev to the parent")],
        description="How Prev/Next walk the chain")
    for c in classes:
        bpy.utils.register_class(c)
    bpy.utils.register_class(EASYBONES_Prefs)
    addon = bpy.context.preferences.addons.get(__package__)
    if addon:
        EASYBONES_PT_panel.bl_category = addon.preferences.panel_category
    for p in PANELS:
        bpy.utils.register_class(p)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        def bind(km, idname, key, pie=False, **mods):
            kmi = km.keymap_items.new("wm.call_menu_pie" if pie else idname, key, 'PRESS', **mods)
            if pie:
                kmi.properties.name = idname
            addon_keymaps.append((km, kmi))

        maps = {name: kc.keymaps.new(name=name) for name in UNBOUND}
        for name, km in maps.items():
            if name != "Weight Paint":
                bind(km, "mesh.easybones_flip", 'BACK_SLASH')
            bind(km, "EASYBONES_MT_select_pie", 'BACK_SLASH', pie=True, shift=True)
        bind(maps["Mesh"], "EASYBONES_MT_pie", 'BACK_SLASH', pie=True, alt=True)
        for name, ops in UNBOUND.items():
            for op in ops:
                bind(maps[name], op, 'NONE')


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    for p in reversed(PANELS):
        bpy.utils.unregister_class(p)
    bpy.utils.unregister_class(EASYBONES_Prefs)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.easybones_armature
    del bpy.types.Scene.easybones_flip
    del bpy.types.Scene.easybones_from
    del bpy.types.Scene.easybones_to
    del bpy.types.Scene.easybones_selected_only
    del bpy.types.Scene.easybones_follow
