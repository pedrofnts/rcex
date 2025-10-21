import threading, io, sys, json, os, traceback, datetime
import FreeSimpleGUI as sg

# Importa seu script principal (mesmo diretório)
import ras_checker  # precisa estar no mesmo diretório

APP_TITLE = "RAS Reservas - Verificador com GUI"
CONFIG_FILE = "ras_gui_config.json"

def load_config(path=CONFIG_FILE):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    # defaults
    return {
        "RAS_USER": "",
        "RAS_PASS": "",
        "RAS_DIA": datetime.date.today().strftime("%d/%m/%Y"),
        "RAS_ANO": str(datetime.date.today().year),
        "RAS_TIMEOUT": "30",
        "RAS_DEBUG_DIR": ".",
        "RAS_ALVOS": ""  # multiline
    }

def save_config(cfg, path=CONFIG_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

class StreamToGUI(io.TextIOBase):
    """Redireciona prints para a GUI (Multiline)."""
    def __init__(self, element):
        self.element = element

    def write(self, s):
        try:
            self.element.write(s)
            # auto-scroll
            try:
                self.element.Widget.see("end")
            except Exception:
                pass
        except Exception:
            pass

    def flush(self):
        pass

def build_layout(cfg):
    left_col = [
        [sg.Text("Usuário (RAS_USER)"), sg.Input(cfg["RAS_USER"], key="-USER-", size=(24,1))],
        [sg.Text("Senha (RAS_PASS)"), sg.Input(cfg["RAS_PASS"], key="-PASS-", password_char="•", size=(24,1))],
        [sg.Text("Dia alvo (dd/mm/aaaa)"), sg.Input(cfg["RAS_DIA"], key="-DIA-", size=(16,1))],
        [sg.Text("Ano padrão"), sg.Input(cfg["RAS_ANO"], key="-ANO-", size=(10,1))],
        [sg.Text("Timeout (s)"), sg.Input(cfg["RAS_TIMEOUT"], key="-TIMEOUT-", size=(10,1))],
        [sg.Text("Pasta de saída (OUTDIR)"),
         sg.Input(cfg["RAS_DEBUG_DIR"], key="-OUTDIR-", expand_x=True),
         sg.FolderBrowse("Escolher")],
        [sg.Text("Alvos (um por linha)")],
        [sg.Multiline(cfg["RAS_ALVOS"], key="-ALVOS-", size=(50,10), expand_x=True)],
        [sg.Button("Salvar Config"), sg.Button("Carregar Config"),
         sg.Push(),
         sg.Button("Executar Verificação", button_color=("white","green"))],
    ]
    right_col = [
        [sg.Text("Saída / Logs")],
        [sg.Multiline("", key="-LOG-", size=(80,25), autoscroll=True, write_only=True,
                      font=("Consolas", 9), expand_x=True, expand_y=True)],
        [sg.Button("Limpar Log"), sg.Push(), sg.Button("Fechar")]
    ]
    layout = [
        [sg.Column(left_col, expand_x=True),
         sg.VSeparator(),
         sg.Column(right_col, expand_x=True, expand_y=True)]
    ]
    return layout

def apply_env_from_window(values):
    # Configura variáveis de ambiente que seu script usa
    os.environ["RAS_USER"] = values["-USER-"].strip()
    os.environ["RAS_PASS"] = values["-PASS-"].strip()
    os.environ["RAS_DIA"] = values["-DIA-"].strip() or datetime.date.today().strftime("%d/%m/%Y")
    os.environ["RAS_ANO"] = values["-ANO-"].strip() or str(datetime.date.today().year)
    os.environ["RAS_TIMEOUT"] = values["-TIMEOUT-"].strip() or "30"
    os.environ["RAS_DEBUG_DIR"] = values["-OUTDIR-"].strip() or "."
    # Preserva quebras de linha nos alvos
    alvos_raw = values["-ALVOS-"]
    os.environ["RAS_ALVOS"] = alvos_raw if isinstance(alvos_raw, str) else ""
    # Debug: mostra o que foi configurado
    print(f"[DEBUG] RAS_ALVOS configurado ({len(os.environ['RAS_ALVOS'])} chars):")
    print(repr(os.environ["RAS_ALVOS"]))

def run_checker_thread(window):
    try:
        # Monkey-patch na função 'pr' do seu script para mandar direto pra GUI
        def gui_pr(x):
            window.write_event_value("-APPEND_LOG-", str(x) + "\n")

        old_pr = getattr(ras_checker, "pr", print)
        setattr(ras_checker, "pr", gui_pr)

        try:
            ras_checker.main()
        finally:
            # restaura pr por segurança
            setattr(ras_checker, "pr", old_pr)

        window.write_event_value("-APPEND_LOG-", "\n[FIM] Execução concluída.\n")
    except Exception as e:
        tb = traceback.format_exc()
        window.write_event_value("-APPEND_LOG-", f"\n[ERRO] {e}\n{tb}\n")

def main():
    # FreeSimpleGUI: define o tema
    sg.theme("SystemDefault")

    layout = build_layout(load_config())
    window = sg.Window(APP_TITLE, layout, resizable=True, finalize=True)

    # Se quiser redirecionar prints deste arquivo para o log:
    # stream_gui = StreamToGUI(window["-LOG-"])
    # sys.stdout = stream_gui
    # sys.stderr = stream_gui

    worker = None

    while True:
        event, values = window.read()
        if event in (sg.WINDOW_CLOSED, "Fechar"):
            break

        if event == "Salvar Config":
            cfg = {
                "RAS_USER": values["-USER-"],
                "RAS_PASS": values["-PASS-"],
                "RAS_DIA": values["-DIA-"],
                "RAS_ANO": values["-ANO-"],
                "RAS_TIMEOUT": values["-TIMEOUT-"],
                "RAS_DEBUG_DIR": values["-OUTDIR-"],
                "RAS_ALVOS": values["-ALVOS-"],
            }
            save_config(cfg)
            sg.popup_ok("Configurações salvas.", title="OK")

        if event == "Carregar Config":
            cfg = load_config()
            for k, v in {
                "-USER-": cfg["RAS_USER"],
                "-PASS-": cfg["RAS_PASS"],
                "-DIA-": cfg["RAS_DIA"],
                "-ANO-": cfg["RAS_ANO"],
                "-TIMEOUT-": cfg["RAS_TIMEOUT"],
                "-OUTDIR-": cfg["RAS_DEBUG_DIR"],
                "-ALVOS-": cfg["RAS_ALVOS"],
            }.items():
                window[k].update(v)
            sg.popup_ok("Configurações carregadas.", title="OK")

        if event == "Limpar Log":
            window["-LOG-"].update("")

        if event == "Executar Verificação":
            # validações simples
            if not values["-USER-"].strip() or not values["-PASS-"].strip().isdigit():
                sg.popup_error("Informe RAS_USER e RAS_PASS (apenas dígitos).")
                continue
            apply_env_from_window(values)
            window["-LOG-"].update("")  # limpa antes de rodar
            # dispara em thread para não travar a GUI
            worker = threading.Thread(target=run_checker_thread, args=(window,), daemon=True)
            worker.start()

        if event == "-APPEND_LOG-":
            window["-LOG-"].write(values["-APPEND_LOG-"])

    window.close()

if __name__ == "__main__":
    main()
