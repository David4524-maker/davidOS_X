# -*- coding: utf-8 -*-
"""
davidOS X ISO Builder  -  para IDLE / Python 3.14  (Run > Run Module, F5)
Coloca este archivo junto a davidOS_X.html.  Descarga Alpine Linux, le inyecta davidOS X
(Chromium en modo kiosco) y genera davidOS_X.iso, arrancable en VMware, VirtualBox, QEMU, Hyper-V (Gen1)...
Requiere internet al construir (y la VM necesita internet en el primer arranque).
"""
import os, re, sys, io, time, gzip, tarfile, hashlib, shutil, subprocess, threading, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
APPDIR = "/opt/davidos"

SESSION = f"""#!/bin/sh
xset s off; xset -dpms; xset s noblank
exec chromium --no-sandbox --user-data-dir=/tmp/davidos-chrome --kiosk --start-fullscreen \\
  --app=file://{APPDIR}/index.html --disable-gpu --noerrdialogs --disable-infobars --no-first-run \\
  --autoplay-policy=no-user-gesture-required --disable-features=Translate --password-store=basic
"""
START = f"""#!/bin/sh
# davidOS X - se ejecuta al arrancar (servicio local)
say() {{ echo "[davidOS X] $*" | tee /dev/console; }}
(
 say "Esperando red..."
 for i in $(seq 1 60); do ping -c1 -W1 dl-cdn.alpinelinux.org >/dev/null 2>&1 && break; sleep 1; done
 say "Instalando componentes graficos (solo la primera vez por arranque)..."
 apk update >/dev/null 2>&1
 apk add --no-progress chromium xorg-server xinit xset eudev mesa-dri-gallium mesa-egl \\
   xf86-input-libinput xf86-video-vesa xf86-video-vmware font-dejavu ttf-freefont font-noto-emoji dbus 2>&1 | tail -n 3
 setup-devd udev >/dev/null 2>&1
 rc-service dbus start >/dev/null 2>&1
 say "Iniciando davidOS X..."
 openvt -s -c 7 -f -- xinit {APPDIR}/session.sh -- /usr/bin/Xorg :0 vt7 -nolisten tcp
) >/dev/null 2>&1 &
"""
INTERFACES = "auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet dhcp\n"
RUNLEVELS = {"sysinit": "devfs dmesg mdev hwdrivers modloop", "boot": "modules sysctl hostname bootmisc syslog networking",
             "default": "local", "shutdown": "mount-ro killprocs savecache"}

def make_apkovl(html_path, branch="latest-stable"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data, mode=0o644):
            ti = tarfile.TarInfo(name); ti.size = len(data); ti.mode = mode; ti.mtime = int(time.time()); tar.addfile(ti, io.BytesIO(data))
        add("etc/hostname", b"davidosx\n")
        add("etc/apk/world", b"alpine-base\n")
        add("etc/apk/repositories", f"{MIRROR}/{branch}/main\n{MIRROR}/{branch}/community\n".encode())
        add("etc/network/interfaces", INTERFACES.encode())
        add("etc/local.d/10-davidos.start", START.encode(), 0o755)
        add(f"{APPDIR[1:]}/session.sh", SESSION.encode(), 0o755)
        with open(html_path, "rb") as f: add(f"{APPDIR[1:]}/index.html", f.read())
        for lvl, svcs in RUNLEVELS.items():
            for s in svcs.split():
                ti = tarfile.TarInfo(f"etc/runlevels/{lvl}/{s}"); ti.type = tarfile.SYMTYPE; ti.linkname = f"/etc/init.d/{s}"; ti.mode = 0o777; tar.addfile(ti)
    return buf.getvalue()

def find_alpine(log):
    log("Buscando la ultima version de Alpine Linux...")
    y = urllib.request.urlopen(f"{MIRROR}/latest-stable/releases/x86_64/latest-releases.yaml", timeout=30).read().decode()
    m = re.search(r"file:\s*(alpine-standard-[\d.]+-x86_64\.iso)\s*\n(?:.*\n){0,12}?\s*sha256:\s*([0-9a-f]{64})", y)
    if not m: raise RuntimeError("No encontre alpine-standard en latest-releases.yaml")
    return m.group(1), m.group(2)

def download(name, sha, dest, log, prog):
    if os.path.exists(dest) and hashlib.sha256(open(dest, "rb").read()).hexdigest() == sha:
        log("ISO base ya descargada y verificada."); return
    log(f"Descargando {name} ..."); r = urllib.request.urlopen(f"{MIRROR}/latest-stable/releases/x86_64/{name}", timeout=60)
    total = int(r.headers.get("Content-Length", 0)); got = 0; h = hashlib.sha256()
    with open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk); h.update(chunk); got += len(chunk)
            if total: prog(got * 100 / total)
    if h.hexdigest() != sha: os.remove(dest); raise RuntimeError("SHA256 incorrecto: descarga corrupta")
    log("Descarga verificada (SHA256 OK).")

def inject(base, apkovl, out, log):
    try: import pycdlib
    except ImportError:
        log("Instalando pycdlib (pip)..."); subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "pycdlib"]); import pycdlib
    tmp = out + ".apkovl.tar.gz"
    with open(tmp, "wb") as f: f.write(apkovl)
    iso = pycdlib.PyCdlib(); iso.open(base)
    kw = {"iso_path": "/DAVIDOS.APK;1"}
    if iso.has_rock_ridge(): kw["rr_name"] = "davidos.apkovl.tar.gz"
    if iso.has_joliet(): kw["joliet_path"] = "/davidos.apkovl.tar.gz"
    iso.add_file(tmp, **kw); log("Escribiendo davidOS_X.iso ..."); iso.write(out); iso.close(); os.remove(tmp)

def build(html, outdir, log=print, prog=lambda p: None):
    if not os.path.isfile(html): raise FileNotFoundError("Falta davidOS_X.html junto a este script")
    os.makedirs(outdir, exist_ok=True); cache = os.path.join(outdir, "alpine_base.iso")
    name, sha = find_alpine(log); download(name, sha, cache, log, prog)
    log("Creando el paquete de configuracion de davidOS X (apkovl)..."); ovl = make_apkovl(html)
    out = os.path.join(outdir, "davidOS_X.iso"); inject(cache, ovl, out, log)
    log(f"LISTO: {out}  ({os.path.getsize(out)//1048576} MB)"); return out

HELP = ("VM recomendada:  Linux 'Other 64-bit' / Alpine, 4 GB RAM, 2 CPU, 128 MB video, red NAT.\n"
        "Monta davidOS_X.iso como CD/DVD y arranca. El 1er arranque descarga Chromium (~1-3 min).")

def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
    root = tk.Tk(); root.title("davidOS X - ISO Builder"); root.geometry("640x480")
    html = tk.StringVar(value=os.path.join(HERE, "davidOS_X.html")); outd = tk.StringVar(value=os.path.join(HERE, "iso_out"))
    for r, (lbl, var, isdir) in enumerate([("davidOS_X.html", html, False), ("Carpeta de salida", outd, True)]):
        ttk.Label(root, text=lbl).grid(row=r, column=0, padx=8, pady=6, sticky="w"); ttk.Entry(root, textvariable=var, width=55).grid(row=r, column=1)
        ttk.Button(root, text="...", width=3, command=lambda v=var, d=isdir: v.set((filedialog.askdirectory() if d else filedialog.askopenfilename(filetypes=[("HTML", "*.html")])) or v.get())).grid(row=r, column=2)
    pb = ttk.Progressbar(root, maximum=100); pb.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=8)
    box = scrolledtext.ScrolledText(root, height=18, state="disabled"); box.grid(row=3, column=0, columnspan=3, padx=8, sticky="nsew")
    root.rowconfigure(3, weight=1); root.columnconfigure(1, weight=1)
    def log(m): root.after(0, lambda: (box.config(state="normal"), box.insert("end", m + "\n"), box.see("end"), box.config(state="disabled")))
    def run():
        btn.config(state="disabled")
        def w():
            try: out = build(html.get(), outd.get(), log, lambda p: root.after(0, lambda: pb.config(value=p))); log(HELP); root.after(0, lambda: messagebox.showinfo("davidOS X", "ISO creada:\n" + out))
            except Exception as e: log("ERROR: " + str(e)); root.after(0, lambda: messagebox.showerror("davidOS X", str(e)))
            finally: root.after(0, lambda: btn.config(state="normal"))
        threading.Thread(target=w, daemon=True).start()
    btn = ttk.Button(root, text="Construir ISO de davidOS X", command=run); btn.grid(row=4, column=0, columnspan=3, pady=8)
    root.mainloop()

if __name__ == "__main__":
    if "--cli" in sys.argv: build(os.path.join(HERE, "davidOS_X.html"), os.path.join(HERE, "iso_out"))
    else: gui()
