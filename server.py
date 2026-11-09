#!/usr/bin/env python3
"""Authoritative threaded TCP server for the LAN collaborative whiteboard."""
import argparse, base64, hashlib, hmac, secrets, socketserver, threading, queue
from datetime import datetime, timezone
from whiteboard.protocol import encode, decode_line, ProtocolError, MAX_MESSAGE
from whiteboard.storage import Store

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Thread-safe activity logging. Cursor updates are intentionally not logged.
LOG_LOCK = threading.Lock()


def log_event(category, message):
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    with LOG_LOCK:
        print(f"[{timestamp}] [{category:<10}] {message}", flush=True)


def client_address(handler):
    try:
        host, port = handler.client_address[:2]
        return f"{host}:{port}"
    except Exception:
        return "unknown client"


def print_server_banner(host, port, db_path):
    print("\n" + "=" * 64, flush=True)
    print("       LAN COLLABORATIVE WHITEBOARD - SERVER", flush=True)
    print("=" * 64, flush=True)
    print(f"  Host      : {host}", flush=True)
    print(f"  Port      : {port}", flush=True)
    print(f"  Database  : {db_path}", flush=True)
    print("=" * 64, flush=True)

def derive(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)

def clean_text(value, limit, allow_empty=False):
    if not isinstance(value, str): raise ValueError("Expected text")
    value=value.strip()
    if (not value and not allow_empty) or len(value)>limit or any(ord(c)<32 and c not in "\n\t" for c in value):
        raise ValueError("Invalid text")
    return value

def valid_color(c):
    return isinstance(c,str) and len(c)==7 and c[0]=="#" and all(x in "0123456789abcdefABCDEF" for x in c[1:])

OPEN_EDITING=False   # --open-editing: any participant may move/edit/delete anyone's objects
REACTIONS=("🎉","👍","❤️","😂","👏","🔥","🤔","✅")
SHAPE_KINDS=("rect","rounded","ellipse","triangle","diamond","frame")
LINE_KINDS=("line","arrow")
STROKE_KINDS=("stroke","highlighter","erase")
ALL_KINDS=STROKE_KINDS+LINE_KINDS+SHAPE_KINDS+("text","sticky","comment","image")
UPDATABLE=("points","text","color","fill","width","w","h","fs","dash","curve")

def validate_op(op, check_data=True):
    """Validate and return a *clean* copy of an operation (unknown keys are dropped)."""
    if not isinstance(op,dict): raise ValueError("Invalid operation")
    kind=op.get("kind")
    if kind not in ALL_KINDS: raise ValueError("Unsupported canvas operation")
    clean={"kind":kind}
    if not valid_color(op.get("color","#202020")): raise ValueError("Invalid color")
    clean["color"]=op.get("color","#202020")
    width=op.get("width",3)
    if isinstance(width,bool) or not isinstance(width,(int,float)) or not 1<=width<=40: raise ValueError("Invalid width")
    clean["width"]=width
    pts=op.get("points",[])
    if not isinstance(pts,list) or len(pts)>5000: raise ValueError("Invalid points")
    for point in pts:
        if not isinstance(point,list) or len(point)!=2 or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and 0<=v<=1 for v in point):
            raise ValueError("Coordinates must be normalized to 0..1")
    clean["points"]=pts
    if kind in STROKE_KINDS and not pts: raise ValueError("Stroke needs points")
    if kind in LINE_KINDS+SHAPE_KINDS and len(pts)!=2: raise ValueError("Shape needs two points")
    if kind in ("text","sticky","comment","image") and len(pts)!=1: raise ValueError("This object needs one anchor point")
    # optional styling fields
    if "fill" in op:
        f=op["fill"]
        if f not in ("",None) and not valid_color(f): raise ValueError("Invalid fill")
        clean["fill"]=f or ""
    for key,lo,hi in (("w",10,6000),("h",10,6000),("fs",6,400)):
        if key in op:
            v=op[key]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not lo<=v<=hi: raise ValueError(f"Invalid {key}")
            clean[key]=v
    for key in ("dash","curve"):
        if key in op:
            if not isinstance(op[key],bool): raise ValueError(f"Invalid {key}")
            clean[key]=op[key]
    # text
    raw=op.get("text","")
    if kind in ("text","comment"): clean["text"]=clean_text(raw,2000)
    elif kind=="sticky": clean["text"]=clean_text(raw,2000,allow_empty=True)
    elif kind in SHAPE_KINDS: clean["text"]=clean_text(raw,500,allow_empty=True) if raw else ""
    else: clean["text"]=""
    if kind=="image" and check_data:
        data=op.get("data","")
        if not isinstance(data,str) or len(data)>1_300_000: raise ValueError("Image too large")
        try: raw_img=base64.b64decode(data,validate=True)
        except Exception as exc: raise ValueError("Invalid image data") from exc
        if not raw_img.startswith((b"\x89PNG\r\n\x1a\n",b"\xff\xd8\xff")): raise ValueError("Only PNG and JPEG images are supported")
        clean["data"]=data
    return clean

class Room:
    def __init__(self,meta,store):
        self.meta,self.store=meta,store
        self.lock=threading.RLock()
        self.events=store.events(meta["code"])
        self.seq=self.events[-1]["seq"] if self.events else 0
        self.clients={}
        self.active=set()
        self.operations={}
        self.operation_seq={}
        self.redo_by_author={}
        for event in self.events:
            action=event.get("action")
            if action=="add":
                op=event["op"]; self.operations[op["id"]]=op; self.operation_seq[op["id"]]=event.get("seq",0); self.active.add(op["id"])
            elif action=="undo":
                target=event.get("id"); self.active.discard(target)
                author=event.get("author","")
                self.redo_by_author.setdefault(author,[]).append(target)
            elif action=="redo":
                target=event.get("id"); self.active.add(target)
                author=event.get("author","")
                stack=self.redo_by_author.setdefault(author,[])
                if target in stack: stack.remove(target)
            elif action=="update":
                target=event.get("id")
                if target in self.operations and isinstance(event.get("changes"),dict):
                    self.operations[target].update(event["changes"])
            elif action=="clear":
                self.active.clear(); self.redo_by_author.clear()
        self.chat=store.chats(meta["code"])
    def snapshot(self):
        # Send compact current state, not the unbounded historical event log.
        items=[]
        for op_id in sorted(self.active,key=lambda key:self.operation_seq.get(key,0)):
            op=dict(self.operations[op_id]); op["active"]=True; items.append(op)
        return {"type":"snapshot","seq":self.seq,"items":items,
                "participants":list(self.clients.values()),"chat":self.chat[-200:]}

class State:
    def __init__(self,db):
        self.store=Store(db); self.rooms={}; self.lock=threading.RLock(); self.create_lock=threading.Lock(); self.create_requests={}
    def get_room(self,code):
        with self.lock:
            if code not in self.rooms:
                meta=self.store.room(code)
                if not meta:return None
                self.rooms[code]=Room(meta,self.store)
            return self.rooms[code]
    def create(self,name,password,request_id=None):
        name=clean_text(name,60)
        if not isinstance(password,str) or not 4<=len(password)<=128:
            raise ValueError("Room password must contain 4–128 characters")
        with self.create_lock:
            password_digest=hashlib.sha256(password.encode()).digest()
            if request_id is not None:
                if not isinstance(request_id, str) or not 8 <= len(request_id) <= 80:
                    raise ValueError("Invalid create request id")
                cached=self.create_requests.get(request_id)
                if cached:
                    if not hmac.compare_digest(cached[2], password_digest):
                        raise ValueError("Create request id was reused with different credentials")
                    return cached[0],cached[1]
            for _ in range(5):
                code=secrets.token_hex(4).upper()
                salt=secrets.token_bytes(16); owner=secrets.token_urlsafe(32)
                try:
                    self.store.create_room(code,name,salt,derive(password,salt),
                        hashlib.sha256(owner.encode()).digest(),now())
                    if request_id is not None:
                        self.create_requests[request_id]=(code,owner,password_digest)
                    return code,owner
                except Exception:
                    continue
        raise ValueError("Could not allocate a room code")

STATE=None

class Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.room=None; self.username=None; self.role=None
        self.client_id=secrets.token_hex(8)
        log_event("CONNECT", f"TCP connection from {client_address(self)}")
        self.outbound=queue.Queue(maxsize=512)
        self.writer_stop=threading.Event()
        self.writer=threading.Thread(target=self._writer_loop,daemon=True)
        self.writer.start()
    def _writer_loop(self):
        while not self.writer_stop.is_set():
            try: item=self.outbound.get(timeout=0.5)
            except queue.Empty: continue
            if item is None: break
            try:
                self.wfile.write(item); self.wfile.flush()
            except OSError:
                try: self.request.shutdown( socketserver.socket.SHUT_RDWR )
                except Exception: pass
                break
    def send(self,obj):
        try:
            data=encode(obj)
            self.outbound.put_nowait(data)
            return True
        except (OSError,ProtocolError,queue.Full):
            try: self.request.shutdown(socketserver.socket.SHUT_RDWR)
            except Exception: pass
            return False
    def finish(self):
        self.writer_stop.set()
        try: self.outbound.put_nowait(None)
        except queue.Full: pass
        if self.writer.is_alive(): self.writer.join(timeout=1.0)
        super().finish()
    def publish_locked(self,room,event):
        """Call with room.lock held: preserves identical event order for every client."""
        room.events.append(event)
        room.store.append_event(room.meta["code"],event["seq"],event)
        for peer in list(room.clients):
            peer.send(event)
    def handle(self):
        try:
            while True:
                line=self.rfile.readline(MAX_MESSAGE+1)
                if not line: break
                try:
                    self.process(decode_line(line))
                except (ValueError,ProtocolError,KeyError,TypeError) as exc:
                    log_event("REJECTED", f"{client_address(self)}: {str(exc)[:160]}")
                    self.send({"type":"error","message":str(exc)[:200]})
        except (ConnectionError,OSError):
            pass
        finally:
            was_in_room = self.room is not None
            self.leave()
            if not was_in_room:
                log_event("DISCONNECT", f"Unjoined client disconnected: {client_address(self)}")
    def process(self,m):
        typ=m["type"]
        if typ=="create":
            if self.room: raise ValueError("Already in a room")
            code,owner=STATE.create(m.get("name","Whiteboard"),m.get("password",""),m.get("request_id"))
            room_name = clean_text(m.get("name","Whiteboard"),60)
            log_event("ROOM", f"Created room {code} ({room_name})")
            self.send({"type":"created","room":code,"owner_token":owner})
            self.join_room(code,m.get("username","Host"),m.get("password",""),owner)
        elif typ=="join":
            if self.room: raise ValueError("Already in a room")
            self.join_room(m.get("room",""),m.get("username",""),m.get("password",""),m.get("owner_token",""))
        elif typ=="sync":
            if not self.room: raise ValueError("Join a room first")
            # Enqueue the snapshot while holding the same lock used for event
            # publication. This guarantees snapshot-before-later-events order.
            with self.room.lock: self.send(self.room.snapshot())
        elif typ=="op": self.canvas_op(m)
        elif typ=="update": self.update_op(m)
        elif typ=="delete": self.delete_op(m)
        elif typ=="undo": self.undo()
        elif typ=="redo": self.redo()
        elif typ=="clear": self.clear()
        elif typ=="chat": self.chat_message(m)
        elif typ=="cursor":
            if self.room:
                p=m.get("point")
                if isinstance(p,list) and len(p)==2 and all(isinstance(v,(int,float)) and 0<=v<=1 for v in p):
                    msg={"type":"cursor","id":self.client_id,"username":self.username,"point":p,"laser":bool(m.get("laser"))}
                    if m.get("emoji") in REACTIONS: msg["emoji"]=m["emoji"]
                    self.broadcast_ephemeral(msg)
        elif typ=="ping": self.send({"type":"pong","time":now()})
        elif typ=="leave": self.leave()
        else: raise ValueError("Unknown message type")
    def broadcast_ephemeral(self,msg):
        room=self.room
        if not room:return
        with room.lock:
            for peer in list(room.clients):
                if peer is not self: peer.send(msg)
    def join_room(self,code,username,password,owner_token):
        code=clean_text(code,20).upper(); username=clean_text(username,24)
        room=STATE.get_room(code)
        if not room: raise ValueError("Room not found")
        meta=room.meta
        if not isinstance(password,str) or len(password)>128:
            raise ValueError("Invalid room password")
        if not hmac.compare_digest(derive(password,meta["salt"]),meta["password_hash"]):
            raise ValueError("Incorrect room password")
        owner_hash=hashlib.sha256(owner_token.encode()).digest() if owner_token else b""
        is_owner=bool(owner_hash) and hmac.compare_digest(owner_hash,meta["owner_token_hash"])
        self.room,self.username,self.role=room,username,("host" if is_owner else "editor")
        with room.lock:
            duplicates=[peer for peer,p in room.clients.items() if p["username"].casefold()==username.casefold()]
            if duplicates:
                # A reconnecting host may replace its stale connection. Ordinary
                # duplicate usernames remain rejected.
                if is_owner and len(duplicates)==1:
                    stale=duplicates[0]
                    room.clients.pop(stale,None)
                    log_event("RECONNECT", f"Replacing stale host connection for {username} in room {code}")
                    try: stale.request.shutdown(socketserver.socket.SHUT_RDWR)
                    except Exception: pass
                else:
                    self.room=None
                    raise ValueError("That username is already active in this room")
            room.clients[self]={"id":self.client_id,"username":username,"role":self.role}
            room_name=meta.get("name","")
            log_event("JOIN", f"{username} ({self.role}) joined room {code} from {client_address(self)}")
            log_event("USERS", f"Room {code}: {len(room.clients)} connected participant(s)")
            self.send({"type":"joined","room":code,"username":username,"role":self.role,
                       "name":room_name,"open_editing":OPEN_EDITING})
            # Snapshot is queued atomically with respect to all canvas events.
            self.send(room.snapshot())
            for peer in list(room.clients):
                if peer is not self:
                    peer.send({"type":"participant_joined","participant":room.clients[self]})
    def leave(self):
        room=self.room
        if not room:return
        with room.lock:
            removed = room.clients.pop(self, None)
            if removed:
                log_event("LEAVE", f"{self.username} left room {room.meta['code']}")
                log_event("USERS", f"Room {room.meta['code']}: {len(room.clients)} connected participant(s) remain")
                for peer in list(room.clients):
                    peer.send({"type":"participant_left","id":self.client_id,"username":self.username})
        self.room=None
    def canvas_op(self,m):
        room=self.room
        if not room: raise ValueError("Join a room first")
        op=validate_op(m.get("op"))
        op.update({"id":secrets.token_hex(12),"author":self.username,"active":True})
        with room.lock:
            room.operations[op["id"]]=op
            room.seq+=1
            room.operation_seq[op["id"]]=room.seq
            event={"type":"canvas_event","seq":room.seq,"action":"add","op":op}
            room.active.add(op["id"]); room.redo_by_author.pop(self.username,None)
            self.publish_locked(room,event)
            log_event("DRAW", f"{self.username} added {op['kind']} in room {room.meta['code']} (seq {room.seq})")
    def can_edit(self,op):
        return OPEN_EDITING or op.get("author")==self.username or self.role=="host"
    def _toggle(self,action,target):
        room=self.room
        if not room: raise ValueError("Not in a room")
        with room.lock:
            op=room.operations.get(target)
            if not op: raise ValueError("Operation not found")
            if not self.can_edit(op):
                raise ValueError("You can only modify your own operations")
            if action=="undo" and target not in room.active: raise ValueError("Operation is already undone")
            if action=="redo" and target in room.active: raise ValueError("Operation is already active")
            room.seq+=1
            ev={"type":"canvas_event","seq":room.seq,"action":action,"id":target,"author":self.username}
            if action=="undo":
                room.active.discard(target); room.redo_by_author.setdefault(self.username,[]).append(target)
            else:
                room.active.add(target)
                stack=room.redo_by_author.setdefault(self.username,[])
                if target in stack: stack.remove(target)
            self.publish_locked(room,ev)
            log_event(action.upper(), f"{self.username} {action} operation {target[:8]} in room {room.meta['code']}")
    def delete_op(self,m):
        """Delete one specific object (it can be restored with redo by the person who deleted it)."""
        target=m.get("id")
        if not isinstance(target,str): raise ValueError("Invalid object id")
        self._toggle("undo",target)
    def update_op(self,m):
        """Move / resize / restyle / re-text one object. Broadcast as an ordered 'update' event."""
        room=self.room
        if not room: raise ValueError("Join a room first")
        target,changes=m.get("id"),m.get("changes")
        if not isinstance(target,str) or not isinstance(changes,dict) or not changes:
            raise ValueError("Invalid update")
        if any(k not in UPDATABLE for k in changes): raise ValueError("Unsupported change")
        with room.lock:
            op=room.operations.get(target)
            if not op or target not in room.active: raise ValueError("Object not found")
            if not self.can_edit(op): raise ValueError("You can only modify your own operations")
            merged=dict(op); merged.update(changes)
            clean=validate_op(merged,check_data=False)
            applied={}
            for k in changes:
                if k not in clean: raise ValueError(f"Cannot change {k} on this object")
                applied[k]=clean[k]
            room.operations[target].update(applied)
            room.seq+=1
            ev={"type":"canvas_event","seq":room.seq,"action":"update","id":target,"changes":applied,"author":self.username}
            self.publish_locked(room,ev)
            log_event("EDIT", f"{self.username} updated an object in room {room.meta['code']} (seq {room.seq})")
    def undo(self):
        room=self.room
        if not room: raise ValueError("Not in a room")
        with room.lock:
            target=next((e["op"]["id"] for e in reversed(room.events)
                if e.get("action")=="add" and e["op"]["author"]==self.username and e["op"]["id"] in room.active),None)
        if not target: raise ValueError("Nothing to undo")
        self._toggle("undo",target)
    def redo(self):
        room=self.room
        if not room: raise ValueError("Not in a room")
        with room.lock:
            stack=room.redo_by_author.get(self.username,[])
            target=next((x for x in reversed(stack) if x not in room.active),None)
        if not target: raise ValueError("Nothing to redo")
        self._toggle("redo",target)
    def clear(self):
        room=self.room
        if not room: raise ValueError("Not in a room")
        if self.role!="host": raise ValueError("Only the host can clear the board")
        with room.lock:
            room.seq+=1; ev={"type":"canvas_event","seq":room.seq,"action":"clear","author":self.username}
            room.active.clear(); room.redo_by_author.clear()
            self.publish_locked(room,ev)
            log_event("CLEAR", f"{self.username} cleared room {room.meta['code']} (seq {room.seq})")
    def chat_message(self,m):
        room=self.room
        if not room: raise ValueError("Join a room first")
        text=clean_text(m.get("text",""),1000); ts=now()
        with room.lock:
            item={"username":self.username,"text":text,"ts":ts}
            item["id"]=room.store.add_chat(room.meta["code"],self.username,text,ts)
            room.chat.append(item); room.chat=room.chat[-500:]
            for peer in list(room.clients): peer.send({"type":"chat_event","message":item})
            log_event("CHAT", f"{self.username} sent a message in room {room.meta['code']}")

class TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address=True; daemon_threads=True; request_queue_size=100

def main():
    global STATE
    ap=argparse.ArgumentParser()
    ap.add_argument("--host",default="0.0.0.0"); ap.add_argument("--port",type=int,default=8765)
    ap.add_argument("--db",default="whiteboard.sqlite3")
    ap.add_argument("--open-editing",action="store_true",
                    help="let every participant move/edit/delete anyone's objects (default: own objects, host: all)")
    args=ap.parse_args()
    global OPEN_EDITING
    OPEN_EDITING=args.open_editing
    if not 1<=args.port<=65535: ap.error("port must be 1..65535")
    STATE=State(args.db)
    print_server_banner(args.host,args.port,args.db)
    with TCPServer((args.host,args.port),Handler) as server:
        log_event("SERVER", f"RUNNING - listening on {args.host}:{args.port}")
        log_event("INFO", "Find this machine's LAN IP and share it with participants.")
        log_event("INFO", "Press Ctrl+C to stop the server.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log_event("SERVER", "Shutdown requested")
        finally:
            log_event("SERVER", "Stopped")


if __name__=="__main__": main()
