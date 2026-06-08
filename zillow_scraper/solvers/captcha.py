#!/usr/bin/env python3
import json, os, random, re, sys, time

os.environ["DISPLAY"] = os.environ.get("DISPLAY", ":1")
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["PYSCREENSHOT_TOOL"] = "scrot"

import pytesseract
import cv2
import numpy as np
from PIL import ImageGrab
from Xlib import display, X, Xatom
from Xlib.ext import xtest

POLL_SEC = 0.5
HOLD_TIMEOUT = 30
DRYRUN = os.environ.get("POC_CAPTCHA_DRYRUN", "1") not in ("0", "false", "False", "")
DEBUG_DIR = os.environ.get("POC_DEBUG_DIR", "/home/rpa/out/debug")
# Espera a pagina assentar apos a 1a deteccao e redetecta p/ coords frescas
# (sem isso o 1o click costuma errar; o 2o acerta).
SETTLE_DELAY = float(os.environ.get("POC_CAPTCHA_SETTLE_SECONDS", "1.5"))
# Tempo apos warp do mouse antes do ButtonPress (mouse assentar no alvo).
WARP_DELAY = float(os.environ.get("POC_CAPTCHA_WARP_SECONDS", "1.0"))

xd = None


def get_disp():
    global xd
    if xd is None:
        xd = display.Display()
    return xd


def maximize_chrome():
    """Encontra a janela do Chrome, maximiza e da foco."""
    d = get_disp()
    root = d.screen().root

    net_client_list = d.intern_atom("_NET_CLIENT_LIST")
    net_wm_name = d.intern_atom("_NET_WM_NAME")
    net_wm_pid = d.intern_atom("_NET_WM_PID")
    net_active_window = d.intern_atom("_NET_ACTIVE_WINDOW")
    wm_state = d.intern_atom("_NET_WM_STATE")
    max_h = d.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ")
    max_v = d.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT")

    def get_name(win):
        try:
            name = win.get_full_text_property(net_wm_name)
            if name and len(name) > 3:
                return name
        except:
            pass
        try:
            name = win.get_wm_name()
            if name and len(name) > 3:
                return name
        except:
            pass
        return None

    client_list = root.get_full_property(net_client_list, 0)
    chrome_win = None
    chrome_name = None
    if client_list:
        for wid in client_list.value:
            try:
                win = d.create_resource_object("window", wid)
                name = get_name(win)
                if name and "google-chrome" in name.lower():
                    chrome_win = win
                    chrome_name = name
                    break
            except:
                continue

    if not chrome_win:
        def walk(child):
            results = []
            try:
                name = get_name(child)
                if name and any(k in name.lower() for k in ["google-chrome", "chrome", "zillow", "rental"]):
                    results.append((name, child))
            except:
                pass
            try:
                for c in child.query_tree().children:
                    results.extend(walk(c))
            except:
                pass
            return results
        wins = walk(root)
        if wins:
            chrome_name, chrome_win = wins[0]

    if not chrome_win:
        print("[captcha] nenhuma janela Chrome encontrada!", flush=True)
        return False

    print(f"[captcha] janela Chrome: [{chrome_name}]", flush=True)

    try:
        ev = X.ClientMessageEvent(
            window=chrome_win, client_type=wm_state,
            data=(32, [1, max_h, max_v, 0, 0]),
        )
        root.send_event(ev, event_mask=X.SubstructureRedirectMask)
        d.sync()
        time.sleep(0.5)
    except Exception as e:
        print(f"[captcha] erro maximizando: {e}", flush=True)

    try:
        geom = chrome_win.get_geometry()
        print(f"[captcha] geometria antes: {geom.width}x{geom.height}+{geom.x}+{geom.y}", flush=True)
        if geom.width < 1200 or geom.height < 700:
            chrome_win.configure(width=1280, height=774, x=0, y=0)
            d.sync()
            time.sleep(0.5)
    except Exception as e:
        print(f"[captcha] erro resize: {e}", flush=True)

    try:
        ev = X.ClientMessageEvent(
            window=chrome_win, client_type=net_active_window,
            data=(32, [1, X.CurrentTime, 0, 0, 0]),
        )
        root.send_event(ev, event_mask=X.SubstructureRedirectMask)
        d.sync()
        print("[captcha] foco enviado via _NET_ACTIVE_WINDOW", flush=True)
    except Exception as e:
        try:
            chrome_win.set_input_focus(X.RevertToParent, X.CurrentTime)
            d.sync()
            print("[captcha] foco via set_input_focus", flush=True)
        except Exception as e2:
            print(f"[captcha] erro foco: {e2}", flush=True)

    time.sleep(0.5)
    try:
        g = chrome_win.get_geometry()
        print(f"[captcha] geometria final: {g.width}x{g.height}+{g.x}+{g.y}", flush=True)
    except:
        pass

    return True


def check_fill(mx, my, lx):
    """Varre da posicao do mouse para a esquerda.
    Retorna True se preenchimento completo (nao volta a ser branco).
    Retorna False se ainda falta (volta a ser branco depois de azul)."""
    scan = np.array(ImageGrab.grab(bbox=(lx, my, mx+1, my+1)))[0]
    in_blue = False
    for i in range(len(scan)-1, -1, -1):
        r, g, b = scan[i]
        w = r > 240 and g > 240 and b > 240
        if not w:
            in_blue = True
        elif in_blue and w:
            return False
    return True


SCALE = 2  # upscale do screenshot antes do OCR


def _ocr_lines(gray):
    """OCR agrupado por linha. Roda em threshold normal e invertido (texto pode
    ser claro-sobre-escuro), upscalado. Devolve lista de
    (text_lower, x, y, w, h) por linha, em coords do grayscale original."""
    big = cv2.resize(gray, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    _, th = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants = [th, cv2.bitwise_not(th)]

    lines = {}
    for vi, img in enumerate(variants):
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        for i in range(len(data["text"])):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            key = (vi, data["block_num"][i], data["par_num"][i], data["line_num"][i])
            x = data["left"][i] // SCALE
            y = data["top"][i] // SCALE
            w = data["width"][i] // SCALE
            h = data["height"][i] // SCALE
            words, x1, y1, x2, y2 = lines.get(key, ([], 1 << 30, 1 << 30, 0, 0))
            words.append(text)
            lines[key] = (words, min(x1, x), min(y1, y), max(x2, x + w), max(y2, y + h))

    out = []
    for words, x1, y1, x2, y2 in lines.values():
        out.append((" ".join(words).lower(), x1, y1, x2 - x1, y2 - y1))
    return out


def find_button():
    """Acha o BOTAO 'Press & Hold' na tela via OCR por linha.
    Retorna (rx, ry, bw, bh) = bbox da linha do botao, ou None.
    Discrimina o botao (texto curto 'Press & Hold') da instrucao longa
    ('Press & Hold to confirm you are a human...')."""
    screen_rgb = np.array(ImageGrab.grab().convert('RGB'))
    gray = cv2.cvtColor(screen_rgb, cv2.COLOR_RGB2GRAY)
    lines = _ocr_lines(gray)

    # Candidatos: 'press' colado em 'hold' (ignorando '&'/espacos/pontuacao).
    # Adjacencia mata ruido onde press/hold aparecem longe (ex: codigo fonte
    # com 'hold'/'press' na mesma linha). Dims plausiveis de botao.
    candidates = []
    for text, x, y, w, h in lines:
        norm = re.sub(r"[^a-z0-9]", "", text)
        if "presshold" in norm and w >= 20 and 8 <= h <= 120:
            candidates.append((norm, x, y, w, h))

    if not candidates:
        return None

    # Ranking: o BOTAO fica sempre ABAIXO da instrucao ("Press & Hold to
    # confirm..."). Escolhe o candidato mais embaixo (maior y). Empate -> texto
    # mais curto (norm == "presshold" puro = botao).
    candidates.sort(key=lambda c: (-c[2], len(c[0])))
    norm, x, y, w, h = candidates[0]
    return (x, y, w, h)


def save_detection(rx, ry, bw, bh, click_x, click_y, ts):
    """Salva screenshot anotado com a box detectada e o ponto de click."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    img = cv2.cvtColor(np.array(ImageGrab.grab().convert('RGB')), cv2.COLOR_RGB2BGR)
    cv2.rectangle(img, (rx, ry), (rx + bw, ry + bh), (0, 0, 255), 2)
    cv2.circle(img, (click_x, click_y), 6, (0, 255, 0), -1)
    path = f"{DEBUG_DIR}/captcha_detect_{ts}.png"
    cv2.imwrite(path, img)
    print(f"[captcha] deteccao anotada: {path}", flush=True)


MOUSE_DRIFT = os.environ.get("POC_MOUSE_DRIFT", "1") not in ("0", "false", "False", "")


def idle_mouse_drift():
    """Move o cursor real devagar p/ ponto aleatorio (presenca humana). Roda so
    quando NAO ha captcha (mesmo processo do solver -> sem conflito com o hold)."""
    if not MOUSE_DRIFT or random.random() > 0.3:
        return
    try:
        d = get_disp()
        scr = d.screen()
        W, H = scr.width_in_pixels, scr.height_in_pixels
        pos = scr.root.query_pointer()
        x0, y0 = pos.root_x, pos.root_y
        x1, y1 = random.randint(0, W - 1), random.randint(0, H - 1)
        steps = random.randint(8, 20)
        for i in range(1, steps + 1):
            x = int(x0 + (x1 - x0) * i / steps)
            y = int(y0 + (y1 - y0) * i / steps)
            scr.root.warp_pointer(x, y)
            d.sync()
            time.sleep(random.uniform(0.01, 0.05))
    except Exception:
        pass


def solve():
    mode = "DRY-RUN (so detecta)" if DRYRUN else "LIVE (click+hold)"
    print(f"[captcha] monitor iniciado - {mode}", flush=True)
    while True:
        if os.environ.get("POC_CAPTCHA_ONLY", "0") not in ("0", "false", "False", ""):
            maximize_chrome()

        result = find_button()
        if result is None:
            idle_mouse_drift()
            time.sleep(POLL_SEC)
            continue

        rx, ry, bw, bh = result
        print(f"[captcha] Botao encontrado! ({rx},{ry}) {bw}x{bh}", flush=True)

        click_x = rx + bw // 2
        click_y = ry + bh // 2
        monitor_x = rx + bw - 25
        monitor_y = click_y

        print(f"[captcha] Click centro: ({click_x}, {click_y})", flush=True)

        ts = int(time.time())

        if DRYRUN:
            save_detection(rx, ry, bw, bh, click_x, click_y, ts)
            time.sleep(2)
            continue

        # Settle: pagina pode estar animando/assentando; espera e redetecta
        # p/ pegar coords frescas (corrige o miss da 1a deteccao).
        time.sleep(SETTLE_DELAY)
        fresh = find_button()
        if fresh is not None:
            rx, ry, bw, bh = fresh
            click_x = rx + bw // 2
            click_y = ry + bh // 2
            monitor_x = rx + bw - 25
            monitor_y = click_y
            print(f"[captcha] redetect pos-settle: ({click_x}, {click_y})", flush=True)

        os.makedirs(DEBUG_DIR, exist_ok=True)
        ImageGrab.grab().save(f"{DEBUG_DIR}/captcha_before_{ts}.png")
        print(f"[captcha] Screenshot before salvo", flush=True)

        d = get_disp()
        d.screen().root.warp_pointer(click_x, click_y)
        d.sync()
        time.sleep(WARP_DELAY)

        xtest.fake_input(d, X.ButtonPress, 1)
        d.sync()
        print("[captcha] ButtonPress!", flush=True)

        start = time.time()
        filled = False
        while time.time() - start < HOLD_TIMEOUT:
            time.sleep(POLL_SEC)
            filled = check_fill(monitor_x, monitor_y, rx)
            t = round(time.time() - start, 1)
            print(f"  t={t}s filled={filled}", flush=True)
            if filled:
                print(f"[captcha] >>> FILL COMPLETO em {t}s!", flush=True)
                time.sleep(3)
                ImageGrab.grab().save(f"{DEBUG_DIR}/captcha_after_{ts}.png")
                print(f"[captcha] Screenshot after salvo", flush=True)
                time.sleep(1)
                break

        xtest.fake_input(d, X.ButtonRelease, 1)
        d.sync()
        print("[captcha] Botao solto...", flush=True)
        print(f"[captcha] CAPTCHA RESOLVIDO! Tempo total: {round(time.time()-start,1)}s", flush=True)

        # Aguarda antes de comecar nova procura
        time.sleep(5)


if __name__ == "__main__":
    solve()
