# SPDX-License-Identifier: GPL-3.0-or-later
"""Transient candidate image using the terminal's existing Kitty graphics plane.

No shadow terminal grid, SKK engine, copied screen cells, saved-cursor slot,
window manager or Fcitx5. Cursor coordinates come from a serialized DSR query.
Never insert an image command inside a child UTF-8/control/image sequence.
"""
import base64
import io
import re
import secrets
import os
import select
import time
from pathlib import Path

class Boundary:
    """Only output boundaries/query ownership, not another terminal emulator."""
    def __init__(self):
        self.state='text'
        self.header=bytearray()
        self.utf8=0
        self.string_kind=0
        self.was_escape=False
        self.graphics_more=False
        self.queries=0
        self.origin=False

    @property
    def safe(self):
        return self.state=='text' and not self.utf8 and not self.graphics_more

    def feed(self,data):
        for b in data:
            if self.state=='text':
                if self.utf8 and 0x80<=b<=0xbf:
                    self.utf8-=1
                    continue
                self.utf8=0
                if b==27: self.state='escape'
                elif 0xc2<=b<=0xdf: self.utf8=1
                elif 0xe0<=b<=0xef: self.utf8=2
                elif 0xf0<=b<=0xf4: self.utf8=3
            elif self.state=='escape':
                self.header.clear()
                if b==ord('['): self.state='csi'
                elif b in b']P_^X':
                    self.state='string';self.string_kind=b;self.was_escape=False
                elif b==27: pass
                elif 0x20<=b<=0x2f: self.state='escape-final'
                else: self.state='text'
            elif self.state=='escape-final':
                if 0x30<=b<=0x7e: self.state='text'
            elif self.state=='csi':
                self.header.append(b)
                if len(self.header)>256: raise ValueError('unbounded child control header')
                if 0x40<=b<=0x7e:
                    c=bytes(self.header)
                    if c in (b'6n', b'?6n'): self.queries+=1
                    if c.startswith(b'?') and c[-1:] in (b'h',b'l') and b'6' in c[1:-1].split(b';'):
                        self.origin=c[-1:]==b'h'
                    self.state='text'
                elif b in (24,26): self.state='text'
            elif self.state=='string':
                ended=(self.was_escape and b==92) or (self.string_kind==93 and b==7)
                if ended:
                    if self.string_kind==95 and self.header.startswith(b'G'):
                        params=bytes(self.header).split(b';',1)[0]
                        self.graphics_more=bool(re.search(rb'(?:^G|,)m=1(?:,|$)',params))
                    self.state='text';self.was_escape=False
                else:
                    # Only retain the control header, never image/clipboard data.
                    if b';' not in self.header:
                        if len(self.header)>=256: raise ValueError('unbounded child string header')
                        self.header.append(b)
                    self.was_escape=(b==27)
        if self.queries>64: raise ValueError('too many unanswered child cursor queries')

class KittyDisplay:
    """One owned image; byte-preserving child output and scoped deletion."""
    def __init__(self,font,rows,cols,image_id=None):
        from PIL import ImageFont
        self.font=ImageFont.truetype(str(Path(font)),18)
        self.rows=rows; self.cols=cols
        self.image_id=image_id if image_id is not None else secrets.randbelow(2**30)+2**30
        self.cursor=None
        self.pending=False
        self.started=None
        self.visible=False
        self.view=None
        self.last=None
        # Previews (#19) only: why they were turned off, whether the pending query is theirs,
        # replies still owed to a query they gave up on, and the cells of the drawn image.
        self.preview_off=None
        self.pending_passive=False
        self.late=0
        self.placed=None
        self.visible_passive=False
        self.boundary=Boundary()

    def initialize(self, write, read_fd=0):
        """Negotiate graphics before the child can exec; preserve typeahead."""
        query=f'\x1b_Ga=q,i={self.image_id},f=24,s=1,v=1;AAAA\x1b\\'.encode()
        write(query)
        response=re.compile(rb'\x1b_Gi='+str(self.image_id).encode()+rb';([^\x1b]*)\x1b\\')
        data=bytearray();deadline=time.monotonic()+1.5
        while time.monotonic()<deadline:
            if not select.select([read_fd],[],[],max(0,deadline-time.monotonic()))[0]: break
            chunk=os.read(read_fd,4096)
            if not chunk: break
            data.extend(chunk)
            if len(data)>1024*1024: raise ValueError('graphics negotiation input limit')
            match=response.search(data)
            if match:
                if match[1]!=b'OK': raise ValueError('terminal rejected Kitty graphics')
                return bytes(data[:match.start()]+data[match.end():])
        raise ValueError('Kitty graphics were not acknowledged; child was not started')

    def clear(self):
        if not self.visible or not self.boundary.safe: return b''
        self.visible=False;self.last=None
        return f'\x1b_Ga=d,d=I,i={self.image_id},q=2\x1b\\'.encode()

    def output(self,data):
        before=self.clear()
        self.boundary.feed(data)
        self.cursor=None
        return before+data

    def resize(self,rows,cols):
        self.rows=rows;self.cols=cols;self.cursor=None;self.last=None

    def reply(self,raw):
        match=re.fullmatch(rb'\x1b\[(\??)([0-9]+);([0-9]+)R',raw)
        if not match: return False
        if self.pending and not match[1]:
            row,col=map(int,(match[2],match[3]))
            if not (1<=row<=self.rows and 1<=col<=self.cols):
                if not self.pending_passive: raise ValueError('cursor report outside current terminal size')
                self.pending=False;self.started=None;self.preview_off='cursor report outside the terminal size'
                return True
            self.cursor=(row,col);self.pending=False;self.started=None
            return True
        if self.late and not match[1]:
            self.late-=1
            return True
        if self.boundary.queries: self.boundary.queries-=1
        return False

    def update(self,view,passive=False):
        """passive: a preview (#19) that must never stop input. It is drawn only above the cursor line,
        and a late cursor or an unusual terminal mode turns previews off instead of failing."""
        self.view=view
        # Cancelling a view does not cancel an already-issued cursor query.
        # Its reply still belongs here, and its wait must remain bounded.
        if (self.started is not None and (self.pending or view is not None)
                and time.monotonic()-self.started>1.5):
            if not passive: raise ValueError('candidate cursor was not established; no invisible confirmation')
            self.late+=int(self.pending);self.pending=False;self.started=None
            self.preview_off='cursor reply later than 1.5 s'
            return self.clear()
        if view is None:
            if not self.pending: self.started=None
            return self.clear()
        if passive and (self.preview_off or self.boundary.origin or self.rows<3 or self.cols<8): return self.clear()
        if self.boundary.origin: raise ValueError('origin-relative cursor mode needs its own proven adapter')
        if self.rows<3 or self.cols<8: raise ValueError('terminal is too small for candidate selection')
        if self.started is None and self.cursor is None: self.started=time.monotonic()
        if not self.boundary.safe or self.boundary.queries or self.pending: return b''
        if self.cursor is None:
            self.pending=True;self.pending_passive=passive
            return b'\x1b[6n'
        signature=(repr(view),passive,self.cursor,self.rows,self.cols)
        if signature==self.last: return b''
        drawn=self.paint(view,passive)
        result=self.clear()+drawn
        self.visible=bool(drawn);self.visible_passive=bool(drawn) and passive;self.last=signature
        return result

    def paint(self,view,passive=False):
        from PIL import Image, ImageDraw
        def plain(value):
            return ''.join(c if ord(c)>=32 and not 0x7f<=ord(c)<=0x9f else ' ' for c in str(value))[:240]
        candidates=view.get('candidates') or []
        selected=view.get('selectedIndex',-1)
        # Core supplies the selected meaning and each candidate's evidence.
        # Reserve rows for those details and the keyboard hint before paging.
        details=[]
        if view.get('meaning'): details.append(plain(view['meaning']))
        notes=view.get('notes') or []
        if 0<=selected<len(notes) and notes[selected]:
            details.append('evidence: '+plain(notes[selected]))
        budget=min(self.rows,8)
        details=details[:max(0,budget-3)]
        count=min(5,max(1,budget-2-len(details)))
        page=max(0,selected)//count*count
        lines=[plain(view.get('text',''))]
        lines += [('▶ ' if i==selected else '  ')+plain(candidates[i]) for i in range(page,min(page+count,len(candidates)))]
        # The Core's view always has its own (Windows) hint; only a preview draws one of its own.
        lines += details+[plain(view['hint']) if passive and view.get('hint') else '↑↓ / Space   Enter: commit   Esc: cancel']
        row,col=self.cursor
        # A preview stays off the cursor line and below, where the shell edits and lists completions.
        if passive and row<=len(lines): return b''
        columns=min(self.cols,60)
        width=columns*10; line_height=26
        image=Image.new('RGB',(width,len(lines)*line_height),(24,26,32))
        draw=ImageDraw.Draw(image)
        for i,line in enumerate(lines):
            draw.text((4,i*line_height+2),line,font=self.font,fill=(240,242,246))
        stream=io.BytesIO();image.save(stream,format='PNG')
        payload=base64.b64encode(stream.getvalue())
        top=row-len(lines) if row>len(lines) else min(row+1,self.rows-len(lines)+1)
        left=max(1,min(col,self.cols-columns+1))
        self.placed=(top,left,len(lines),columns)
        out=bytearray(f'\x1b[{top};{left}H'.encode())
        for offset in range(0,len(payload),4096):
            more=int(offset+4096<len(payload))
            control=f'a=T,f=100,t=d,i={self.image_id},C=1,z=2147483647,c={columns},r={len(lines)},q=2,m={more}' if offset==0 else f'm={more}'
            out.extend(b'\x1b_G'+control.encode()+b';'+payload[offset:offset+4096]+b'\x1b\\')
        out.extend(f'\x1b[{row};{col}H'.encode())
        return bytes(out)
