import os, re, sys, datetime, json
from urllib.parse import urljoin, urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup

LOGIN_URL = "https://rasweb.pcivil.rj.gov.br/p_login.aspx"
RESERVAS_PATH = "/FRMRESERVARVAGASERVIDOR.ASPX"
ABERTURA_PATH = "/Abertura.aspx"
ENCERRA_PATH = "/Encerra.aspx"

DIA_ALVO_BR = os.environ.get("RAS_DIA", "22/11/2025")
OUTDIR = os.environ.get("RAS_DEBUG_DIR", ".")
TIMEOUT = int(os.environ.get("RAS_TIMEOUT", "30"))
ALVOS_INPUT = os.environ.get("RAS_ALVOS", "").strip()
ANO_PADRAO = os.environ.get("RAS_ANO", str(datetime.date.today().year))
AUTO_RESERVA = os.environ.get("RAS_AUTO_RESERVA", "1").strip() not in ("0","false","False","no","n")

def pr(x): print(x, flush=True)

def dump(name, resp_or_text, suffix="html"):
    try:
        if hasattr(resp_or_text, "text"):
            text = resp_or_text.text
            code = getattr(resp_or_text, "status_code", "")
            url = getattr(resp_or_text, "url", "")
            fname = os.path.join(OUTDIR, f"{name}_{code}.{suffix}")
        else:
            text = str(resp_or_text)
            url = ""
            fname = os.path.join(OUTDIR, f"{name}.{suffix}")
        os.makedirs(os.path.dirname(fname) or ".", exist_ok=True)
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text)
        pr(f"[dump] {name} -> {fname} ({len(text)} bytes){' | ' + url if url else ''}")
    except Exception as e:
        pr(f"[dump] falha {name}: {e}")

def bs(html): return BeautifulSoup(html, "html.parser")

def ensure_creds():
    u = os.environ.get("RAS_USER","").strip()
    p = os.environ.get("RAS_PASS","").strip()
    if not u or not p or not p.isdigit():
        pr("Defina RAS_USER e RAS_PASS (apenas dígitos).")
        sys.exit(1)
    return u, p

def extract_hidden_map(html, names=None):
    names = names or ["__VIEWSTATE","__VIEWSTATEGENERATOR","__EVENTVALIDATION","__VIEWSTATEENCRYPTED","usopk","__EVENTTARGET","__EVENTARGUMENT","ctl00$ScriptManager1_HiddenField"]
    s = bs(html)
    out = {}
    for n in names:
        el = s.find("input", {"name": n})
        out[n] = el.get("value","") if el else ""
    return out

def print_hidden_summary(tag, hmap):
    pr(f"[{tag}] hidden:")
    for k,v in hmap.items():
        preview = (v[:40] + "...") if len(v)>40 else v
        pr(f"   - {k}: len={len(v)} | {preview}")

def msajax_redirect(text):
    token = "pageRedirect||"
    if token in text:
        after = text.split(token,1)[1]
        url_enc = after.split("|",1)[0]
        return unquote(url_enc)
    return None

def lotacoes(html):
    s = bs(html)
    sel = s.find("select", {"id":"LBO_lotacao"})
    if not sel: return []
    return [(o.get("value",""), o.get_text(strip=True)) for o in sel.find_all("option")]

def to_iso(dia_br):
    if re.match(r"^\d{2}/\d{2}/\d{4}$", dia_br):
        return datetime.datetime.strptime(dia_br, "%d/%m/%Y").date().isoformat()
    if re.match(r"^\d{2}/\d{2}$", dia_br):
        d, m = dia_br.split("/")
        return datetime.date(int(ANO_PADRAO), int(m), int(d)).isoformat()
    raise ValueError("Data inválida: " + dia_br)

def normalize_date_iso(date_str):
    if not date_str or date_str == "00/00/0000":
        return None
    try:
        parts = date_str.split('-')
        if len(parts) == 3:
            y, m, d = parts
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except:
        pass
    return None

def extract_available_dates_from_json(json_text):
    try:
        data = json_text if isinstance(json_text, dict) else eval(json_text.replace('null', 'None').replace('true', 'True').replace('false', 'False'))
        dates_str = data.get('d', '')
        dates = [d.strip().strip('"') for d in dates_str.split(',') if d.strip()]
        normalized = [normalize_date_iso(d) for d in dates]
        unique_dates = sorted(set([d for d in normalized if d]))
        return unique_dates
    except Exception as e:
        pr(f"[ERROR] Falha ao extrair datas do JSON: {e}")
        return []

def reservas_hidden_ids(html):
    s = bs(html)
    ids = ["ctl00_CPC_dps_hdanomesref","ctl00_CPC_dps_hdtipoperfilvaga","ctl00_CPC_dps_hddepoid","ctl00_CPC_dps_hdusuaid","ctl00_CPC_dps_hddias"]
    out = {}
    for i in ids:
        el = s.find("input", {"id": i})
        out[i] = el.get("value","") if el else ""
    return out

def extract_delta_hidden(ms_text):
    out = {}
    for m in re.finditer(r"\|hiddenField\|(__VIEWSTATEGENERATOR|__VIEWSTATE|__EVENTVALIDATION)\|([^\|]*)\|", ms_text):
        out[m.group(1)] = m.group(2)
    return out

def extract_rows_with_buttons(ms_text):
    m = re.search(r"(<table[^>]+id=\"ctl00_CPC_dps_data_reserva_grd_dia\"[\s\S]+?</table>)", ms_text, re.I)
    if not m:
        m = re.search(r"(<table[\s\S]+?</table>)", ms_text, re.I)
        if not m:
            return [], "", []
    table_html = m.group(1)
    sdoc = bs(table_html)
    rows = []
    btns = []
    trs = sdoc.find_all("tr")
    for tr in trs:
        tds = tr.find_all("td")
        if len(tds) >= 4:
            data = tds[0].get_text(strip=True)
            periodo = tds[1].get_text(strip=True)
            orgao = tds[2].get_text(strip=True)
            perfil = tds[3].get_text(strip=True)
            if data.lower() == "data":
                continue
            btn_el = None
            if len(tds) > 4:
                btn_el = tds[4].find("input", {"type": re.compile("submit", re.I)})
                if not btn_el:
                    btn_el = tds[4].find("input", {"value": re.compile("Confirmar", re.I)})
            btn_name = btn_el.get("name") if btn_el else None
            btn_value = btn_el.get("value") if btn_el else None
            rows.append({"data": data, "periodo": periodo, "orgao": orgao, "perfil": perfil, "disponivel": bool(btn_el)})
            btns.append({"name": btn_name, "value": btn_value})
    return rows, table_html, btns

def base_of(url):
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"

def pick_uso_pk_from_url(url):
    try:
        q = parse_qs(urlparse(url).query)
        if "uso_pk" in q and q["uso_pk"]:
            return q["uso_pk"][0]
    except: pass
    return None

def sniff_uso_pk_from_text(html):
    hits = re.findall(r"/Abertura\.aspx\?uso_pk=(\d+)", html)
    if hits: return hits[0]
    hits2 = re.findall(r"/Encerra\.aspx\?uso_pk=(\d+)", html)
    if hits2: return hits2[0]
    return None

def is_login_page(html):
    s = bs(html)
    if s.find(id="login") and "TELA DE AUTENTICAÇÃO" in s.get_text(" ", strip=True).upper():
        return True
    return False

def is_duplicate_session(html):
    return "EXISTE OUTRA CONEX" in html.upper() and "ABERTA PARA ESTE USU" in html.upper()

def print_cookies(tag, jar):
    pairs = [f"{c.name}={c.value}" for c in jar]
    pr(f"[cookies:{tag}] {'; '.join(pairs) if pairs else '(vazio)'}")

def follow_msajax_with_fallback(session, url_candidate, label, timeout=TIMEOUT):
    try:
        pr(f"[{label}] follow -> {url_candidate}")
        r = session.get(url_candidate, timeout=timeout, allow_redirects=True)
        pr(f"[{label}] code={r.status_code} url={r.url}")
        return r
    except requests.exceptions.RequestException as e:
        pr(f"[{label}] falha direta: {e}")
        u = urlparse(url_candidate)
        if u.port == 9510 or u.scheme == "http":
            fallback = f"https://rasweb.pcivil.rj.gov.br{u.path}"
            if u.query:
                fallback += f"?{u.query}"
            try:
                pr(f"[{label}] fallback -> {fallback}")
                r2 = session.get(fallback, timeout=timeout, allow_redirects=True)
                pr(f"[{label}] fallback code={r2.status_code} url={r2.url}")
                return r2
            except requests.exceptions.RequestException as e2:
                pr(f"[{label}] fallback falhou: {e2}")
        return None

def montar_mapping_pinpad(soup):
    mapping = {}
    for i in range(1, 6):
        a = soup.select_one(f"#tecla_number_0{i}")
        if not a:
            continue
        titulo = (a.get("title") or a.text or "").strip()
        digitos = [d.strip() for d in re.split(r"[-|]", titulo) if d.strip()]
        for d in digitos:
            if d.isdigit() and len(d) == 1:
                mapping[d] = str(i)
    if len(mapping) < 10:
        raise RuntimeError(f"Pinpad incompleto: mapeei {len(mapping)}/10 dígitos: {mapping}")
    return mapping

def codificar_senha(pinpad_map, senha_real):
    seq = []
    for ch in senha_real:
        if ch not in pinpad_map:
            raise ValueError(f"Dígito '{ch}' não encontrado no pinpad atual.")
        seq.append(pinpad_map[ch])
    return "".join(seq)

def parse_alvos(texto):
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    alvos = []
    for l in linhas:
        l1 = l.replace("–", "-").replace("—", "-")
        m = re.match(r"^\s*(\d{1,2}/\d{1,2})(?:/\d{2,4})?\s*-\s*(.+?)\s*-\s*([0-2]\d:[0-5]\d\s*-\s*[0-2]\d:[0-5]\d)\s*$", l1, re.I)
        if not m:
            continue
        data_raw, orgao_txt, periodo = m.groups()
        if re.match(r"^\d{2}/\d{2}$", data_raw):
            data_br = f"{data_raw}/{ANO_PADRAO}"
        else:
            data_br = data_raw
        alvos.append({"data_br": data_br, "orgao_req": orgao_txt.strip(), "periodo": periodo.strip()})
    return alvos

def orgao_key_from_req(orgao_req):
    orgao_req = orgao_req.strip()
    if re.search(r"\bDEAM\b", orgao_req, re.I):
        return {"tipo":"DEAM", "texto":orgao_req}
    m = re.search(r"(\d{1,3})", orgao_req)
    if m:
        n = int(m.group(1))
        return {"tipo":"DPNUM", "num":n}
    return {"tipo":"TEXTO", "texto":orgao_req}

def matches_orgao(orgao_row, key):
    t = orgao_row.strip()
    if key["tipo"] == "DEAM":
        return "DEAM" in t.upper()
    if key["tipo"] == "DPNUM":
        n = key["num"]
        pat = rf"\b0?{n:02d}a\.?\s*\.?\s*Delegacia"
        return re.search(pat, t, re.I) is not None
    if key["tipo"] == "TEXTO":
        return key["texto"].lower() in t.lower()
    return False

def fetch_rows_for_date(session, base_url, uso_pk, dia_br, anomesref_hint=None):
    rpage = session.get(base_url + RESERVAS_PATH, timeout=TIMEOUT)
    dump("05b_reservas_again", rpage)
    reservas_hidden = extract_hidden_map(rpage.text, names=["__VIEWSTATE","__VIEWSTATEGENERATOR","__EVENTVALIDATION","ctl00$ScriptManager1_HiddenField"])
    dps_hidden = reservas_hidden_ids(rpage.text)
    anomesref = dps_hidden.get("ctl00_CPC_dps_hdanomesref","") or (anomesref_hint or "")
    depoid = dps_hidden.get("ctl00_CPC_dps_hddepoid","") or "0"
    usuaid = dps_hidden.get("ctl00_CPC_dps_hdusuaid","")
    tipoperfilvaga = dps_hidden.get("ctl00_CPC_dps_hdtipoperfilvaga","") or "3"
    reservas_url_final = base_url + RESERVAS_PATH
    getuc_url = reservas_url_final + "/GetUserControl"
    headers_json = {
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*",
        "Origin": base_url,
        "Referer": reservas_url_final,
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    payload_json = {"anomesref": anomesref or re.sub(r"[^0-9]","", datetime.datetime.strptime(dia_br, "%d/%m/%Y").strftime("%Y%m")), "tipoperfilvaga": tipoperfilvaga, "depoid": depoid, "usuaid": usuaid}
    r_getuc = session.post(getuc_url, headers=headers_json, data=json.dumps(payload_json), timeout=TIMEOUT)
    dump("06b_getusercontrol", r_getuc)
    available_dates = extract_available_dates_from_json(r_getuc.text)
    dates_raw_str = ",".join(f"\"{d}\"" for d in available_dates)
    dia_iso = to_iso(dia_br)
    script_field = "ctl00$CPC$dps$upd_tela_resultado|ctl00$CPC$dps$btninvocadetalhe"
    payload_async = {
        "ctl00$ScriptManager1": script_field,
        "ctl00_ScriptManager1_HiddenField": "",
        "ctl00$usopk": uso_pk or "",
        "ctl00$CPC$dps$txt_mes_de_referencia": datetime.datetime.strptime(dia_br, "%d/%m/%Y").strftime("%B/%Y").capitalize(),
        "ctl00$CPC$dps$drpTipoPerfil": tipoperfilvaga,
        "ctl00$CPC$dps$txt_hora_mes": "120",
        "ctl00$CPC$dps$txt_horas_util_mes": "72",
        "ctl00$CPC$dps$txt_vagas_disp": "48",
        "ctl00$CPC$dps$drp_selecione_delegacia": depoid,
        "ctl00$CPC$dps$hdanomesref": anomesref,
        "ctl00$CPC$dps$hdtipoperfilvaga": tipoperfilvaga,
        "ctl00$CPC$dps$hddepoid": depoid,
        "ctl00$CPC$dps$hdusuaid": usuaid,
        "ctl00$CPC$dps$hddias": dates_raw_str,
        "ctl00$CPC$dps$hddiaselecionado": dia_iso,
        "ctl00$CPC$dps$hdnPostControl": "0",
        "ctl00$CPC$dps$data_reserva$hdusuaid": usuaid,
        "ctl00$CPC$dps$txtjustificativaCancelaReserva": "",
        "ctl00$CPC$dps$hdcabcelareservavagaid": "",
        "__EVENTTARGET": "ctl00$CPC$dps$btninvocadetalhe",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": reservas_hidden.get("__VIEWSTATE",""),
        "__VIEWSTATEGENERATOR": reservas_hidden.get("__VIEWSTATEGENERATOR",""),
        "__EVENTVALIDATION": reservas_hidden.get("__EVENTVALIDATION",""),
        "__VIEWSTATEENCRYPTED": "",
        "__ASYNCPOST": "true",
        "ctl00$CPC$dps$btninvocadetalhe": "ctl00$CPC$dps$btninvocadetalhe",
    }
    headers_ajax = {
        "X-Requested-With":"XMLHttpRequest",
        "X-MicrosoftAjax":"Delta=true",
        "Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",
        "Accept":"*/*",
        "Origin": base_url,
        "Referer": reservas_url_final,
    }
    rday = session.post(reservas_url_final, data=payload_async, headers=headers_ajax, timeout=TIMEOUT)
    dump(f"07b_post_async_{dia_br.replace('/','-')}", rday)
    hidden_delta = extract_delta_hidden(rday.text)
    rows, table_html, btns = extract_rows_with_buttons(rday.text)
    if table_html:
        dump(f"09b_table_{dia_br.replace('/','-')}", table_html, suffix="fragment.html")
    hidden_final = {
        "__VIEWSTATE": hidden_delta.get("__VIEWSTATE", reservas_hidden.get("__VIEWSTATE","")),
        "__VIEWSTATEGENERATOR": hidden_delta.get("__VIEWSTATEGENERATOR", reservas_hidden.get("__VIEWSTATEGENERATOR","")),
        "__EVENTVALIDATION": hidden_delta.get("__EVENTVALIDATION", reservas_hidden.get("__EVENTVALIDATION","")),
    }
    return rows, btns, hidden_final, dps_hidden, reservas_url_final, dia_iso

def reserve_row(session, base_url, uso_pk, hidden_fields, dps_hidden, reservas_url_final, dia_iso, btn_name, btn_value):
    payload = {
        "ctl00$ScriptManager1": f"ctl00$CPC$dps$upd_tela_resultado|{btn_name}",
        "ctl00_ScriptManager1_HiddenField": "",
        "ctl00$usopk": uso_pk or "",
        "__EVENTTARGET": btn_name,
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": hidden_fields.get("__VIEWSTATE",""),
        "__VIEWSTATEGENERATOR": hidden_fields.get("__VIEWSTATEGENERATOR",""),
        "__EVENTVALIDATION": hidden_fields.get("__EVENTVALIDATION",""),
        "__VIEWSTATEENCRYPTED": "",
        "__ASYNCPOST": "true",
        btn_name: btn_value or "",
        "ctl00$CPC$dps$hdanomesref": dps_hidden.get("ctl00_CPC_dps_hdanomesref",""),
        "ctl00$CPC$dps$hdtipoperfilvaga": dps_hidden.get("ctl00_CPC_dps_hdtipoperfilvaga",""),
        "ctl00$CPC$dps$hddepoid": dps_hidden.get("ctl00_CPC_dps_hddepoid",""),
        "ctl00$CPC$dps$hdusuaid": dps_hidden.get("ctl00_CPC_dps_hdusuaid",""),
        "ctl00$CPC$dps$hddias": "",
        "ctl00$CPC$dps$hddiaselecionado": dia_iso,
        "ctl00$CPC$dps$hdnPostControl": "0",
        "ctl00$CPC$dps$data_reserva$hdusuaid": dps_hidden.get("ctl00_CPC_dps_hdusuaid",""),
        "ctl00$CPC$dps$txtjustificativaCancelaReserva": "",
        "ctl00$CPC$dps$hdcabcelareservavagaid": "",
    }
    headers_ajax = {
        "X-Requested-With":"XMLHttpRequest",
        "X-MicrosoftAjax":"Delta=true",
        "Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",
        "Accept":"*/*",
        "Origin": base_url,
        "Referer": reservas_url_final,
    }
    r = session.post(reservas_url_final, data=payload, headers=headers_ajax, timeout=TIMEOUT)
    dump("10_reserva_post", r)
    ok = ("RESERVA EFETUADA" in r.text.upper()) or ("RESERVADA" in r.text.upper()) or ("SUCESSO" in r.text.upper())
    return ok, r

def main():
    user, senha = ensure_creds()
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/120 Safari/537.36"})
    uso_pk = None
    try:
        pr("[STEP] GET login")
        r0 = s.get(LOGIN_URL, timeout=TIMEOUT)
        pr(f"  url={r0.url} code={r0.status_code}")
        print_cookies("get_login", s.cookies)
        dump("01_get_login", r0)
        h0 = extract_hidden_map(r0.text)
        print_hidden_summary("login_get", h0)
        pinpad_map = montar_mapping_pinpad(bs(r0.text))
        pr(f"[pinpad] mapeamento extraído: {pinpad_map}")
        senha_codificada = codificar_senha(pinpad_map, senha)
        pr(f"[pinpad] senha codificada: {senha_codificada}")
        payload_login = {
            "__LASTFOCUS":"",
            "__EVENTTARGET":"entrar",
            "__EVENTARGUMENT":"",
            "__VIEWSTATE":h0.get("__VIEWSTATE",""),
            "__VIEWSTATEGENERATOR":h0.get("__VIEWSTATEGENERATOR",""),
            "__EVENTVALIDATION":h0.get("__EVENTVALIDATION",""),
            "txtusuario":user,
            "senha":senha_codificada,
            "usopk":h0.get("usopk","-1"),
        }
        pr("[STEP] POST login")
        pr("  headers: Content-Type=application/x-www-form-urlencoded")
        r1 = s.post(LOGIN_URL, data=payload_login, timeout=TIMEOUT)
        pr(f"  url={r1.url} code={r1.status_code}")
        print_cookies("post_login", s.cookies)
        dump("02_post_login", r1)
        if is_duplicate_session(r1.text):
            pr("[INFO] Sessão duplicada detectada. Assumindo sessão anterior...")
            h1_dup = extract_hidden_map(r1.text)
            print_hidden_summary("duplicate_session", h1_dup)
            payload_confirm = {
                "__EVENTTARGET":"entrar2",
                "__EVENTARGUMENT":"",
                "__VIEWSTATE":h1_dup.get("__VIEWSTATE",""),
                "__VIEWSTATEGENERATOR":h1_dup.get("__VIEWSTATEGENERATOR",""),
                "__EVENTVALIDATION":h1_dup.get("__EVENTVALIDATION",""),
                "txtusuario":user,
                "senha":senha_codificada,
                "usopk":h1_dup.get("usopk","-1"),
            }
            pr("[STEP] POST confirmação de assumir sessão")
            r_confirm = s.post(LOGIN_URL, data=payload_confirm, timeout=TIMEOUT)
            pr(f"  url={r_confirm.url} code={r_confirm.status_code}")
            print_cookies("post_confirm_session", s.cookies)
            dump("02b_post_confirm_session", r_confirm)
            pr("[INFO] Refazendo login após assumir sessão...")
            h_new = extract_hidden_map(r_confirm.text)
            print_hidden_summary("login_after_confirm", h_new)
            pinpad_map_new = montar_mapping_pinpad(bs(r_confirm.text))
            pr(f"[pinpad] novo mapeamento extraído: {pinpad_map_new}")
            senha_codificada_new = codificar_senha(pinpad_map_new, senha)
            pr(f"[pinpad] nova senha codificada: {senha_codificada_new}")
            payload_login_new = {
                "__LASTFOCUS":"",
                "__EVENTTARGET":"entrar",
                "__EVENTARGUMENT":"",
                "__VIEWSTATE":h_new.get("__VIEWSTATE",""),
                "__VIEWSTATEGENERATOR":h_new.get("__VIEWSTATEGENERATOR",""),
                "__EVENTVALIDATION":h_new.get("__EVENTVALIDATION",""),
                "txtusuario":user,
                "senha":senha_codificada_new,
                "usopk":h_new.get("usopk","-1"),
            }
            pr("[STEP] POST login (após assumir sessão)")
            r1 = s.post(LOGIN_URL, data=payload_login_new, timeout=TIMEOUT)
            pr(f"  url={r1.url} code={r1.status_code}")
            print_cookies("post_login_retry", s.cookies)
            dump("02c_post_login_retry", r1)
        red1 = msajax_redirect(r1.text)
        if red1:
            r1r = follow_msajax_with_fallback(s, red1, "03_follow_login_redirect")
            if r1r: dump("03_follow_login_redirect", r1r)
        else:
            r1r = r1
        lot = lotacoes(r1r.text)
        if lot:
            pr(f"[INFO] Tela de lotação: {len(lot)} opção(ões)")
            for v,t in lot: pr(f"  - value={v} | {t}")
            choice_val = lot[0][0]
            h1 = extract_hidden_map(r1r.text)
            print_hidden_summary("lotacao_get", h1)
            payload_lot = {
                "__EVENTTARGET":"Entrar_lotacao",
                "__EVENTARGUMENT":"",
                "__VIEWSTATE":h1.get("__VIEWSTATE",""),
                "__VIEWSTATEGENERATOR":h1.get("__VIEWSTATEGENERATOR",""),
                "__EVENTVALIDATION":h1.get("__EVENTVALIDATION",""),
                "txtusuario":user,
                "senha":"",
                "usopk":"",
                "LBO_lotacao":choice_val,
            }
            pr("[STEP] POST seleção de lotação")
            r2 = s.post(LOGIN_URL, data=payload_lot, timeout=TIMEOUT)
            pr(f"  url={r2.url} code={r2.status_code}")
            print_cookies("post_lotacao", s.cookies)
            dump("04_post_lotacao", r2)
            uso_pk = pick_uso_pk_from_url(r2.url) or sniff_uso_pk_from_text(r2.text) or uso_pk
        base_url = "https://rasweb.pcivil.rj.gov.br"
        if not ALVOS_INPUT:
            alvos = [{"data_br": DIA_ALVO_BR, "orgao_req": "", "periodo": ""}]
        else:
            alvos = parse_alvos(ALVOS_INPUT)
        pr("[ALVOS] " + json.dumps(alvos, ensure_ascii=False))
        resultados = []
        for alvo in alvos:
            data_br = alvo["data_br"]
            rows, btns, hidden_fields, dps_hidden, reservas_url_final, dia_iso = fetch_rows_for_date(s, base_url, uso_pk, data_br)
            key = orgao_key_from_req(alvo["orgao_req"]) if alvo["orgao_req"] else None
            periodo_req = alvo["periodo"]
            dia_fmt = datetime.datetime.strptime(data_br, "%d/%m/%Y").strftime("%d/%m/%Y")
            match_idx = None
            match_row = None
            match_btn = None
            for idx, r in enumerate(rows, start=1):
                cond_data = r["data"].strip() == dia_fmt
                cond_periodo = (periodo_req == "" or r["periodo"].strip() == periodo_req)
                cond_orgao = True if not key else matches_orgao(r["orgao"], key)
                if cond_data and cond_periodo and cond_orgao:
                    match_idx = idx
                    match_row = r
                    match_btn = btns[idx-1] if idx-1 < len(btns) else None
                    break
            if match_row:
                resultados.append({"data": data_br, "orgao_req": alvo["orgao_req"], "periodo": periodo_req, "linha": match_idx, "disponivel": match_row["disponivel"], "orgao_real": match_row["orgao"]})
                pr(f"[TARGET] {data_br} - {alvo['orgao_req']} - {periodo_req} -> {'DISPONÍVEL' if match_row['disponivel'] else 'OCUPADA'} (linha {match_idx})")
                if AUTO_RESERVA and match_row["disponivel"] and match_btn and match_btn.get("name"):
                    pr(f"[RESERVA] Disparando reserva da linha {match_idx} ({match_btn['name']})")
                    ok, r = reserve_row(s, base_url, uso_pk, hidden_fields, dps_hidden, reservas_url_final, dia_iso, match_btn["name"], match_btn.get("value"))
                    pr(f"[RESERVA] Resultado: {'OK' if ok else 'NOK'} | code={r.status_code}")
            else:
                resultados.append({"data": data_br, "orgao_req": alvo["orgao_req"], "periodo": periodo_req, "linha": None, "disponivel": False, "orgao_real": None})
                pr(f"[TARGET] {data_br} - {alvo['orgao_req']} - {periodo_req} -> NÃO ENCONTRADO")
        pr("\n=== Verificação de alvos ===")
        for r in resultados:
            status = "✓ DISPONÍVEL" if r["disponivel"] else "✗ Indisponível/Não encontrado"
            linha = f"linha {r['linha']}" if r["linha"] else "linha ?"
            org = f" | {r['orgao_real']}" if r["orgao_real"] else ""
            pr(f"{r['data']} - {r['orgao_req']} - {r['periodo']} -> {status} ({linha}){org}")
    finally:
        try:
            if not uso_pk:
                uso_pk = sniff_uso_pk_from_text(locals().get("r1", type("x",(object,),{"text":""})()).text if "r1" in locals() else "")
            if uso_pk:
                enc_url = urljoin("https://rasweb.pcivil.rj.gov.br", f"{ENCERRA_PATH}?uso_pk={uso_pk}")
                pr(f"[LOGOUT] GET {enc_url}")
                r_out = s.get(enc_url, timeout=TIMEOUT)
                pr(f"[LOGOUT] code={r_out.status_code} url={r_out.url}")
                dump("99_logout", r_out)
            else:
                pr("[LOGOUT] uso_pk não identificado; nada a encerrar.")
        except Exception as e:
            pr(f"[LOGOUT] falha: {e}")

if __name__ == "__main__":
    main()
