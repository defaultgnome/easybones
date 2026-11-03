# Run: /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python test_easybones.py
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import easybones  # noqa: E402

assert easybones.increment("finger.01.L") == "finger.02.L"
assert easybones.increment("spine9") == "spine10"
assert easybones.increment("arm.L") == "arm.L"
names = ["spine.01", "spine.02", "spine.03", "arm.01.L", "arm.02.L", "arm.01.R", "tail", "a2", "a10"]
assert easybones.chain_step(names, "spine.01", 1) == "spine.02"
assert easybones.chain_step(names, "spine.03", 1) == "spine.01"  # wraps
assert easybones.chain_step(names, "spine.01", -1) == "spine.03"
assert easybones.chain_step(names, "arm.02.L", 1) == "arm.01.L"  # other side is its own chain
assert easybones.chain_step(names, "arm.01.R", 1) is None  # alone in its chain
assert easybones.chain_step(names, "tail", 1) is None  # no number
assert easybones.chain_step(names, "a2", 1) == "a10"  # numeric, not alphabetical


def close(a, b):
    return (Vector(a) - Vector(b)).length < 1e-4


def ring_cylinder(a, b, r=0.3, n=8, loops=5):
    """Points of a cylinder from a to b, denser near a (uneven loops must not bias the axis)."""
    a, b = Vector(a), Vector(b)
    d = (b - a).normalized()
    u = d.orthogonal().normalized()
    v = d.cross(u)
    pts = []
    for i in range(loops):
        f = (i / (loops - 1)) ** 2
        c = a.lerp(b, f)
        pts += [c + r * (math.cos(t) * u + math.sin(t) * v)
                for t in (2 * math.pi * k / n for k in range(n))]
    return pts


O = Vector()
h, t = easybones.span(ring_cylinder((3, 1, 4), (1, 2, 0)), O)  # tilted: head = lower end
assert close(h, (1, 2, 0)) and close(t, (3, 1, 4)), (h, t)
h, t = easybones.span(ring_cylinder((6, 0, 1), (2, 0, 1.5)), O)  # near-horizontal: head nearer origin
assert close(h, (2, 0, 1.5)) and close(t, (6, 0, 1)), (h, t)
h, t = easybones.span(ring_cylinder((6, 0, 1), (2, 0, 1.5)), Vector((10, 0, 0)))
assert close(h, (6, 0, 1)), h

easybones.register()
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)

bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=2, location=(1, 1, 3), rotation=(0.5, 0.3, 0))
cyl = bpy.context.object
cyl_axis = cyl.matrix_world.to_3x3() @ Vector((0, 0, 1))
cyl_ends = sorted((cyl.location + cyl_axis, cyl.location - cyl_axis), key=lambda p: p.z)
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
cube = bpy.context.object
bpy.ops.object.armature_add(location=(5, 0, 0))
arm = bpy.context.object
arm.rotation_euler.z = 1.0  # transformed armature: positions must still land in world space
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.armature.select_all(action='SELECT')
bpy.ops.armature.delete()
bpy.ops.object.mode_set(mode='OBJECT')

bpy.context.scene.easybones_armature = arm


def edit(ob):
    bpy.ops.object.mode_set(mode='OBJECT')
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')


edit(cube)


def select(pred, ob=cube):
    bm = bmesh.from_edit_mesh(ob.data)
    for v in bm.verts:
        v.select_set(pred(v.co))
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)


def world(bone, attr):
    return arm.matrix_world @ getattr(bone, attr)


def bones():
    return arm.data.bones


def expect_error(msg, op, **kw):
    try:
        op(**kw)
    except RuntimeError as e:
        assert msg in str(e), e
        return
    raise AssertionError(f"expected error: {msg}")


select(lambda co: co.z < 0)  # bottom face -> center (0,0,-1)
bpy.ops.mesh.easybones_new(name="leg.01.L")
b = bones()["leg.01.L"]
assert close(world(b, "head_local"), (0, 0, -1)), world(b, "head_local")
assert b.parent is None

select(lambda co: co.z > 0)  # top face -> (0,0,1)
bpy.ops.mesh.easybones_tail()
assert close(world(b, "tail_local"), (0, 0, 1))

select(lambda co: co.x > 0 and co.y > 0 and co.z > 0)  # one corner
bpy.ops.mesh.easybones_extend(name=easybones.increment(bones().active.name))
c = bones()["leg.02.L"]
assert c.parent == b and c.use_connect
assert close(world(c, "tail_local"), (1, 1, 1))
assert bones().active == c
assert bpy.context.mode == 'EDIT_MESH' and bpy.context.object == cube

# Head of a connected bone drags its parent's tail along
select(lambda co: co.x < 0 and co.y < 0 and co.z > 0)  # (-1,-1,1)
bpy.ops.mesh.easybones_head()
b, c = bones()["leg.01.L"], bones()["leg.02.L"]
assert close(world(c, "head_local"), (-1, -1, 1)) and close(world(b, "tail_local"), (-1, -1, 1))
assert c.use_connect

# Tail of the parent drags connected children's heads
bones().active = b
select(lambda co: co.z > 0 and co.x > 0 and co.y < 0)  # (1,-1,1)
bpy.ops.mesh.easybones_tail()
b, c = bones()["leg.01.L"], bones()["leg.02.L"]
assert close(world(b, "tail_local"), (1, -1, 1)) and close(world(c, "head_local"), (1, -1, 1))

# Would collapse leg.01 (its head is at (0,0,-1)): refused, nothing moved
select(lambda co: co.z < 0)
bones().active = c
expect_error("zero length", bpy.ops.mesh.easybones_head)  # via connected parent's tail
bones().active = b
expect_error("zero length", bpy.ops.mesh.easybones_tail)
assert close(world(bones()["leg.02.L"], "head_local"), (1, -1, 1))

# New bones are unparented; Move translates the whole bone, connected neighbors follow
select(lambda co: co.z < 0)  # (0,0,-1)
bpy.ops.mesh.easybones_new(name="knee")
k = bones()["knee"]
assert k.parent is None
k_vec = k.tail_local - k.head_local
select(lambda co: co.z > 0)  # (0,0,1)
bpy.ops.mesh.easybones_move()
k = bones()["knee"]
assert close(world(k, "head_local"), (0, 0, 1)) and close(k.tail_local - k.head_local, k_vec)

bones().active = bones()["leg.02.L"]
c = bones()["leg.02.L"]
c_vec = c.tail_local - c.head_local
select(lambda co: co.x < 0 and co.y < 0 and co.z > 0)  # (-1,-1,1)
bpy.ops.mesh.easybones_move()
b, c = bones()["leg.01.L"], bones()["leg.02.L"]
assert close(world(c, "head_local"), (-1, -1, 1)) and close(world(b, "tail_local"), (-1, -1, 1))
assert close(c.tail_local - c.head_local, c_vec) and c.use_connect

# Span a tilted cylinder: head at the lower end, invert flips
edit(cyl)
select(lambda co: True, cyl)
bpy.ops.mesh.easybones_span(name="arm")
s = bones()["arm"]
assert close(world(s, "head_local"), cyl_ends[0]) and close(world(s, "tail_local"), cyl_ends[1])
bpy.ops.mesh.easybones_span(name="arm.inv", invert=True)
s = bones()["arm.inv"]
assert close(world(s, "head_local"), cyl_ends[1])

# Span refits the active bone instead
bones().active = bones()["knee"]
bpy.ops.mesh.easybones_span(target='ACTIVE')
k = bones()["knee"]
assert close(world(k, "head_local"), cyl_ends[0]) and close(world(k, "tail_local"), cyl_ends[1])

# Flip round trip
bpy.ops.mesh.easybones_flip()
assert bpy.context.mode == 'EDIT_ARMATURE' and bpy.context.object == arm
bpy.ops.mesh.easybones_flip()
assert bpy.context.mode == 'EDIT_MESH' and bpy.context.object == cyl

select(lambda co: False, cyl)
expect_error("No vertices selected", bpy.ops.mesh.easybones_tail)

# Bone selection: mirror / next / prev, in mesh edit (active bone only), armature edit and pose
bpy.ops.object.mode_set(mode='OBJECT')
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='EDIT')
for n in ("DEF_a.L", "DEF_a.R", "DEF_b.01", "DEF_b.02", "DEF_b.03", "ORG_x"):
    arm.data.edit_bones.new(n).tail = (0, 0, 1)
arm.data.edit_bones.active = arm.data.edit_bones["DEF_a.L"]
bpy.ops.armature.easybones_select_mirror()
assert arm.data.edit_bones.active.name == "DEF_a.R" and arm.data.edit_bones["DEF_a.R"].select
assert not arm.data.edit_bones["DEF_a.L"].select
bpy.ops.armature.easybones_select_next()  # no number: warns, active stays
assert arm.data.edit_bones.active.name == "DEF_a.R"
arm.data.edit_bones.active = arm.data.edit_bones["DEF_b.03"]
bpy.ops.armature.easybones_select_next()
assert arm.data.edit_bones.active.name == "DEF_b.01"
bpy.ops.armature.easybones_select_prev()
assert arm.data.edit_bones.active.name == "DEF_b.03"
arm.data.edit_bones.active = arm.data.edit_bones["ORG_x"]
bpy.ops.armature.easybones_select_mirror()  # no mirror: warns, active stays
assert arm.data.edit_bones.active.name == "ORG_x"

# Copy to Layer: DEF -> ORG in an ORG collection; a conflict does nothing
arm.data.edit_bones.active = arm.data.edit_bones["DEF_b.01"]
arm.data.edit_bones["DEF_b.02"].parent = arm.data.edit_bones["DEF_b.01"]
bpy.ops.armature.easybones_copy_to_layer(from_prefix="DEF", to_prefix="ORG")
ebs = arm.data.edit_bones
assert all(f"ORG_{n}" in ebs for n in ("a.L", "a.R", "b.01", "b.02", "b.03"))
assert ebs["ORG_b.02"].parent == ebs["ORG_b.01"] and not ebs["ORG_b.01"].use_deform
assert [c.name for c in ebs["ORG_a.L"].collections] == ["ORG"]
n_bones = len(ebs)
expect_error("Name conflict", bpy.ops.armature.easybones_copy_to_layer, from_prefix="DEF", to_prefix="ORG")
assert len(arm.data.edit_bones) == n_bones

# Link Transforms: Copy Transforms DEF -> ORG, idempotent
bpy.ops.object.mode_set(mode='POSE')
bpy.ops.pose.easybones_link_transforms(from_prefix="DEF", to_prefix="ORG")
con = arm.pose.bones["DEF_a.L"].constraints[0]
assert con.type == 'COPY_TRANSFORMS' and con.target == arm and con.subtarget == "ORG_a.L"
assert bpy.ops.pose.easybones_link_transforms(from_prefix="DEF", to_prefix="ORG") == {'CANCELLED'}  # nothing new
assert len(arm.pose.bones["DEF_a.L"].constraints) == 1

# Follow parenting: Next = first child, Prev = parent
bpy.ops.object.mode_set(mode='POSE')
bpy.context.scene.easybones_follow = 'PARENTING'
arm.data.bones.active = arm.data.bones["DEF_b.01"]
bpy.ops.armature.easybones_select_next()
assert arm.data.bones.active.name == "DEF_b.02"
bpy.ops.armature.easybones_select_prev()
assert arm.data.bones.active.name == "DEF_b.01"
bpy.context.scene.easybones_follow = 'NAMING'

# Copy Transform from edit mode (hops to pose and back), Clone Layer from pose mode
bpy.ops.object.mode_set(mode='EDIT')
arm.data.edit_bones.new("DEF_z").tail = (0, 0, 1)
arm.data.edit_bones.new("ORG_z").tail = (0, 0, 1)
bpy.ops.pose.easybones_link_transforms(from_prefix="DEF_z", to_prefix="ORG_z")
assert bpy.context.mode == 'EDIT_ARMATURE'
bpy.ops.object.mode_set(mode='POSE')
assert arm.pose.bones["DEF_z"].constraints[0].subtarget == "ORG_z"
expect_error("Name conflict", bpy.ops.armature.easybones_copy_to_layer, from_prefix="DEF_b", to_prefix="ORG_b")
assert bpy.context.mode == 'POSE'

# Flip works from pose mode too, back to the meshes it came from
edit(cyl)
bpy.ops.mesh.easybones_flip()
bpy.ops.object.mode_set(mode='POSE')
bpy.ops.mesh.easybones_flip()
assert bpy.context.mode == 'EDIT_MESH' and bpy.context.object == cyl
assert easybones.EASYBONES_PT_panel.bl_category == "Rigging"
easybones.unregister()
print("EASYBONES OK")
