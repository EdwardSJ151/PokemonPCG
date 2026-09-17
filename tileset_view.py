"""Compose Emerald metatiles (primary+secondary) into a viewable contact sheet."""
import struct
from pathlib import Path
from PIL import Image, ImageDraw

BASE = Path('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokeemerald/data/tilesets')

def load(path):
    mt = (path/'metatiles.bin').read_bytes()
    img = Image.open(path/'tiles.png').convert('P')
    pals = []
    for i in range(16):
        p = path/'palettes'/f'{i:02d}.pal'
        cols = [(0,0,0)]*16
        if p.exists():
            ls = p.read_text().split('\n')[3:]
            c = [tuple(int(x) for x in l.split()) for l in ls if l.strip()]
            cols = (c+[(0,0,0)]*16)[:16]
        pals.append(cols)
    return mt, img, pals

class TS:
    def __init__(self, prim, sec=None):
        self.pmt, self.pimg, self.ppals = load(prim)
        self.smt, self.simg, self.spals = (load(sec) if sec else (None,None,None))
        # primary owns palettes 0-5, secondary 6-15
        self.pals = list(self.ppals)
        if self.spals:
            for i in range(6,16): self.pals[i] = self.spals[i]
    def tile(self, idx, pal, xf, yf):
        img = self.pimg if idx < 512 else self.simg
        i = idx if idx < 512 else idx-512
        if img is None: return Image.new('RGBA',(8,8),(0,0,0,0))
        w = img.size[0]//8
        if i >= w*(img.size[1]//8): return Image.new('RGBA',(8,8),(0,0,0,0))
        t = img.crop(((i%w)*8,(i//w)*8,(i%w)*8+8,(i//w)*8+8))
        if xf: t = t.transpose(Image.FLIP_LEFT_RIGHT)
        if yf: t = t.transpose(Image.FLIP_TOP_BOTTOM)
        px=t.load(); out=Image.new('RGBA',(8,8),(0,0,0,0)); op=out.load()
        for y in range(8):
            for x in range(8):
                v=px[x,y]&0xF
                op[x,y]=(0,0,0,0) if v==0 else (*self.pals[pal][v],255)
        return out
    def metatile(self, gid):
        src, base = (self.pmt, gid) if gid < 512 else (self.smt, gid-512)
        out = Image.new('RGBA',(16,16),(0,0,0,255))
        if src is None or (base+1)*16 > len(src): return out
        for layer in range(2):
            for j in range(4):
                v = struct.unpack_from('<H', src, base*16+2*(layer*4+j))[0]
                idx,xf,yf,pal = v&0x3FF,(v>>10)&1,(v>>11)&1,(v>>12)&0xF
                if layer and idx==0: continue
                out.alpha_composite(self.tile(idx,pal,xf,yf), ((j%2)*8,(j//2)*8))
        return out

def sheet(ts, ids, out, cols=16, scale=3, labels=None):
    cell=16*scale+14; rows=(len(ids)+cols-1)//cols
    sh=Image.new('RGB',(cols*cell,rows*cell),(40,40,40)); d=ImageDraw.Draw(sh)
    for n,i in enumerate(ids):
        m=ts.metatile(i).convert('RGB').resize((16*scale,)*2,Image.NEAREST)
        x,y=(n%cols)*cell,(n//cols)*cell; sh.paste(m,(x,y))
        d.text((x+1,y+16*scale+1), labels[n] if labels else hex(i), fill=(255,255,120))
    sh.save(out); print(out, sh.size, len(ids))
