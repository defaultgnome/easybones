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
bpy.ops.mesh.easybones_new(name="leg.01.L", roll='GLOBAL_POS_Z')
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

# Auto parent: scene target applies to new bones, keep offset
bpy.context.scene.easybones_parent = "leg.01.L"
bpy.ops.mesh.easybones_new(name="knee", parent="leg.01.L")
k = bones()["knee"]
assert k.parent.name == "leg.01.L" and not k.use_connect
expect_error("No bone named", bpy.ops.mesh.easybones_new, name="x", parent="nope")
assert "x" not in bones()

# Parent active: cycle refused, then clear
bones().active = bones()["leg.01.L"]
expect_error("or its child", bpy.ops.mesh.easybones_parent, parent="knee")
bones().active = bones()["knee"]
bpy.ops.mesh.easybones_parent(parent="leg.02.L", pick=True)
assert bones()["knee"].parent.name == "leg.02.L"
assert bpy.context.scene.easybones_parent == "leg.02.L"
bpy.ops.mesh.easybones_parent(parent="")
assert bones()["knee"].parent is None

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
assert easybones.EASYBONES_PT_panel.bl_category == "Rigging"
easybones.unregister()
print("EASYBONES OK")
