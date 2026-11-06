import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pptx import Presentation
from pptx.util import Emu

p = Presentation(r"C:\Users\张恒\Desktop\开题报告\开题报告答辩PPT(1).pptx")
print("slide_size:", p.slide_width, p.slide_height, "=", p.slide_width/914400, "x", p.slide_height/914400, "in")
print("num_slides:", len(p.slides))
print("="*90)

def walk(shapes, depth=0, sl=None):
    for sh in shapes:
        ind = "  "*depth
        try:
            top = round(sh.top/914400,2); left = round(sh.left/914400,2)
            w = round(sh.width/914400,2); h = round(sh.height/914400,2)
        except Exception:
            top=left=w=h=None
        print(f"{ind}[{sh.shape_type}] name={sh.name!r} pos=({left},{top}) size=({w}x{h})")
        if sh.shape_type == 6 or sh.__class__.__name__ == 'GroupShape':
            try:
                walk(sh.shapes, depth+1)
                continue
            except Exception as e:
                print(ind, "group walk fail", e)
        if sh.has_text_frame:
            for para in sh.text_frame.paragraphs:
                runs = []
                for r in para.runs:
                    sz = r.font.size.pt if r.font.size else None
                    b = r.font.bold
                    runs.append(f"{{sz={sz},b={b}}}{r.text}")
                t = "".join(runs).strip()
                if t:
                    print(f"{ind}  P lvl={para.level}: {t}")
        if getattr(sh, "has_table", False) and sh.has_table:
            tbl = sh.table
            print(f"{ind}  TABLE {len(tbl.rows)}x{len(tbl.columns)}")
            for ri,row in enumerate(tbl.rows):
                cells = [c.text.replace("\n"," / ") for c in row.cells]
                print(f"{ind}   R{ri}: {cells}")

for i, slide in enumerate(p.slides, 1):
    print(f"\n########## SLIDE {i}  layout={slide.slide_layout.name!r}")
    walk(slide.shapes)
    if slide.has_notes_slide:
        nt = slide.notes_slide.notes_text_frame.text.strip()
        if nt:
            print("  --- NOTES:", nt)
