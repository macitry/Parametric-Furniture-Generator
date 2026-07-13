import ezdxf
path=r"C:/Users/mac/Desktop/project/Parametric Furniture Generator/library/profiles/3030.dxf"
doc=ezdxf.readfile(path)
verts=[]
for block in doc.blocks:
 if block.name.startswith("*"):continue
 for e in block:
  if e.dxftype()=="LINE":
   verts.append((e.dxf.start[0],e.dxf.start[1]))
   verts.append((e.dxf.end[0],e.dxf.end[1]))
cx=sum(v[0]for v in verts)/len(verts)
cy=sum(v[1]for v in verts)/len(verts)
print(f"Center:({cx:.1f},{cy:.1f})")
for block in doc.blocks:
 if block.name.startswith("*"):continue
 for e in block:
  t=e.dxftype()
  if t=="LINE":
   e.dxf.start=(e.dxf.start[0]-cx,e.dxf.start[1]-cy)
   e.dxf.end=(e.dxf.end[0]-cx,e.dxf.end[1]-cy)
  elif t in("CIRCLE","ARC"):
   e.dxf.center=(e.dxf.center[0]-cx,e.dxf.center[1]-cy)
doc.saveas(path)
print("Saved")
