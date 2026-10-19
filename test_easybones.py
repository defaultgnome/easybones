# Run: /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python test_easybones.py
import os
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import easybones  # noqa: E402

assert easybones.increment("finger.01.L") == "finger.02.L"
assert easybones.increment("spine9") == "spine10"
assert easybones.increment("arm.L") == "arm.L"

easybones.register()
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)

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
bpy.context.scene.easybones_roll = 'GLOBAL_POS_Z'
arm.select_set(False)
cube.select_set(True)
bpy.context.view_layer.objects.active = cube
bpy.ops.object.mode_set(mode='EDIT')


def select(pred):
    bm = bmesh.from_edit_mesh(cube.data)
    for v in bm.verts:
        v.select_set(pred(v.co))
    bm.select_flush_mode()
    bmesh.update_edit_mesh(cube.data)


def world(bone, attr):
    return arm.matrix_world @ getattr(bone, attr)


def close(a, b):
    return (Vector(a) - Vector(b)).length < 1e-4


select(lambda co: co.z < 0)  # bottom face -> center (0,0,-1)
bpy.ops.mesh.easybones_new(name="leg.01.L")
b = arm.data.bones["leg.01.L"]
assert close(world(b, "head_local"), (0, 0, -1)), world(b, "head_local")

select(lambda co: co.z > 0)  # top face -> (0,0,1)
bpy.ops.mesh.easybones_tail()
assert close(world(b, "tail_local"), (0, 0, 1))

select(lambda co: co.x > 0 and co.y > 0 and co.z > 0)  # one corner
bpy.ops.mesh.easybones_extend(name=easybones.increment(arm.data.bones.active.name))
c = arm.data.bones["leg.02.L"]
assert c.parent == b and c.use_connect
assert close(world(c, "tail_local"), (1, 1, 1))
assert arm.data.bones.active == c
assert bpy.context.mode == 'EDIT_MESH' and bpy.context.object == cube

select(lambda co: False)
try:
    bpy.ops.mesh.easybones_tail()
    raise AssertionError("expected error")
except RuntimeError as e:
    assert "No vertices selected" in str(e)
assert easybones.EASYBONES_PT_panel.bl_category == "Rigging"
easybones.unregister()
print("EASYBONES OK")
