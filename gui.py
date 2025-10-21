import threading, io, sys, json, os, traceback, datetime, time
import FreeSimpleGUI as sg

# Importa seu script principal (mesmo diretório)
import ras_checker  # precisa estar no mesmo diretório

APP_TITLE = "ras-ex"
CONFIG_FILE = "ras_gui_config.json"

# Variável global para controlar o agendamento
scheduled_thread = None
cancel_scheduled = False

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
        "RAS_ALVOS": "",  # multiline
        "AUTO_RESERVA": True,
        "AGENDAR_ENABLED": False,
        "AGENDAR_DATA": datetime.date.today().strftime("%d/%m/%Y"),
        "AGENDAR_HORA": "00:00"
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
        [sg.Text("Usuário"), sg.Input(cfg["RAS_USER"], key="-USER-", size=(24,1))],
        [sg.Text("Senha"), sg.Input(cfg["RAS_PASS"], key="-PASS-", password_char="•", size=(24,1))],
        [sg.Text("Dia alvo (dd/mm/aaaa)"), sg.Input(cfg["RAS_DIA"], key="-DIA-", size=(16,1))],
        [sg.Text("Ano padrão"), sg.Input(cfg["RAS_ANO"], key="-ANO-", size=(10,1))],
        [sg.Text("Timeout (s)"), sg.Input(cfg["RAS_TIMEOUT"], key="-TIMEOUT-", size=(10,1))],
        [sg.Checkbox("Efetuar reserva automaticamente", default=cfg.get("AUTO_RESERVA", True), key="-AUTO_RESERVA-")],
        [sg.Text("Alvos (um por linha)")],
        [sg.Multiline(cfg["RAS_ALVOS"], key="-ALVOS-", size=(50,10), expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Checkbox("Agendar execução", default=cfg.get("AGENDAR_ENABLED", False), key="-AGENDAR_ENABLED-", enable_events=True)],
        [sg.Text("Data (dd/mm/aaaa)"), sg.Input(cfg.get("AGENDAR_DATA", datetime.date.today().strftime("%d/%m/%Y")), key="-AGENDAR_DATA-", size=(12,1), disabled=not cfg.get("AGENDAR_ENABLED", False)),
         sg.Text("Hora (HH:MM)"), sg.Input(cfg.get("AGENDAR_HORA", "00:00"), key="-AGENDAR_HORA-", size=(8,1), disabled=not cfg.get("AGENDAR_ENABLED", False))],
        [sg.Text("Status:", size=(8,1)), sg.Text("Nenhum agendamento ativo", key="-STATUS_AGENDAMENTO-", text_color="gray")],
        [sg.Button("Salvar Config"), sg.Button("Carregar Config"),
         sg.Push(),
         sg.Button("Executar Verificação", button_color=("white","green")),
         sg.Button("Cancelar Agendamento", button_color=("white","red"), disabled=True, key="-CANCELAR-")],
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
    os.environ["RAS_DEBUG_DIR"] = "."  # sempre usa diretório atual
    os.environ["RAS_AUTO_RESERVA"] = "1" if values["-AUTO_RESERVA-"] else "0"
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

def scheduled_checker_thread(window, target_datetime):
    """Thread que aguarda até o horário agendado e executa a verificação"""
    global cancel_scheduled

    try:
        window.write_event_value("-APPEND_LOG-", f"\n[AGENDAMENTO] Execução agendada para {target_datetime.strftime('%d/%m/%Y às %H:%M')}\n")
        window.write_event_value("-UPDATE_STATUS-", f"Agendado para {target_datetime.strftime('%d/%m/%Y às %H:%M')}")

        # Loop até chegar o horário ou cancelar
        while not cancel_scheduled:
            now = datetime.datetime.now()
            time_diff = (target_datetime - now).total_seconds()

            if time_diff <= 0:
                # Chegou a hora!
                window.write_event_value("-APPEND_LOG-", "\n[AGENDAMENTO] Iniciando execução agendada...\n")
                window.write_event_value("-UPDATE_STATUS-", "Executando agendamento...")
                window.write_event_value("-SCHEDULE_COMPLETE-", True)
                run_checker_thread(window)
                break

            # Atualiza o status a cada minuto
            hours = int(time_diff // 3600)
            minutes = int((time_diff % 3600) // 60)
            seconds = int(time_diff % 60)

            if hours > 0:
                window.write_event_value("-UPDATE_STATUS-",
                    f"Agendado: faltam {hours}h {minutes}m {seconds}s")
            elif minutes > 0:
                window.write_event_value("-UPDATE_STATUS-",
                    f"Agendado: faltam {minutes}m {seconds}s")
            else:
                window.write_event_value("-UPDATE_STATUS-",
                    f"Agendado: faltam {seconds}s")

            # Aguarda 1 segundo antes de verificar novamente
            time.sleep(1)

        if cancel_scheduled:
            window.write_event_value("-APPEND_LOG-", "\n[AGENDAMENTO] Agendamento cancelado pelo usuário.\n")
            window.write_event_value("-UPDATE_STATUS-", "Agendamento cancelado")
            window.write_event_value("-SCHEDULE_CANCELLED-", True)

    except Exception as e:
        tb = traceback.format_exc()
        window.write_event_value("-APPEND_LOG-", f"\n[ERRO AGENDAMENTO] {e}\n{tb}\n")
        window.write_event_value("-SCHEDULE_ERROR-", True)

def main():
    global scheduled_thread, cancel_scheduled

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
            # Cancela agendamento se houver
            cancel_scheduled = True
            break

        if event == "-AGENDAR_ENABLED-":
            # Habilita/desabilita os campos de agendamento
            enabled = values["-AGENDAR_ENABLED-"]
            window["-AGENDAR_DATA-"].update(disabled=not enabled)
            window["-AGENDAR_HORA-"].update(disabled=not enabled)

        if event == "Salvar Config":
            cfg = {
                "RAS_USER": values["-USER-"],
                "RAS_PASS": values["-PASS-"],
                "RAS_DIA": values["-DIA-"],
                "RAS_ANO": values["-ANO-"],
                "RAS_TIMEOUT": values["-TIMEOUT-"],
                "RAS_ALVOS": values["-ALVOS-"],
                "AUTO_RESERVA": values["-AUTO_RESERVA-"],
                "AGENDAR_ENABLED": values["-AGENDAR_ENABLED-"],
                "AGENDAR_DATA": values["-AGENDAR_DATA-"],
                "AGENDAR_HORA": values["-AGENDAR_HORA-"],
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
                "-ALVOS-": cfg["RAS_ALVOS"],
                "-AUTO_RESERVA-": cfg.get("AUTO_RESERVA", True),
                "-AGENDAR_ENABLED-": cfg.get("AGENDAR_ENABLED", False),
                "-AGENDAR_DATA-": cfg.get("AGENDAR_DATA", datetime.date.today().strftime("%d/%m/%Y")),
                "-AGENDAR_HORA-": cfg.get("AGENDAR_HORA", "00:00"),
            }.items():
                window[k].update(v)
            # Atualiza estado dos campos
            enabled = cfg.get("AGENDAR_ENABLED", False)
            window["-AGENDAR_DATA-"].update(disabled=not enabled)
            window["-AGENDAR_HORA-"].update(disabled=not enabled)
            sg.popup_ok("Configurações carregadas.", title="OK")

        if event == "Limpar Log":
            window["-LOG-"].update("")

        if event == "Executar Verificação":
            # validações simples
            if not values["-USER-"].strip() or not values["-PASS-"].strip().isdigit():
                sg.popup_error("Informe RAS_USER e RAS_PASS (apenas dígitos).")
                continue

            apply_env_from_window(values)

            # Verifica se deve agendar ou executar imediatamente
            if values["-AGENDAR_ENABLED-"]:
                try:
                    # Parse da data e hora
                    data_str = values["-AGENDAR_DATA-"].strip()
                    hora_str = values["-AGENDAR_HORA-"].strip()

                    # Valida formato
                    target_date = datetime.datetime.strptime(data_str, "%d/%m/%Y").date()
                    target_time = datetime.datetime.strptime(hora_str, "%H:%M").time()
                    target_datetime = datetime.datetime.combine(target_date, target_time)

                    # Verifica se não é no passado
                    if target_datetime <= datetime.datetime.now():
                        sg.popup_error("A data/hora agendada deve ser no futuro!")
                        continue

                    # Cancela agendamento anterior se existir
                    cancel_scheduled = True
                    if scheduled_thread and scheduled_thread.is_alive():
                        scheduled_thread.join(timeout=2)

                    # Reseta flag de cancelamento
                    cancel_scheduled = False

                    # Inicia nova thread de agendamento
                    scheduled_thread = threading.Thread(
                        target=scheduled_checker_thread,
                        args=(window, target_datetime),
                        daemon=True
                    )
                    scheduled_thread.start()

                    # Habilita botão de cancelar
                    window["-CANCELAR-"].update(disabled=False)

                except ValueError as e:
                    sg.popup_error(f"Data/Hora inválida! Use formato dd/mm/aaaa e HH:MM\nErro: {e}")
                    continue
            else:
                # Execução imediata
                window["-LOG-"].update("")  # limpa antes de rodar
                worker = threading.Thread(target=run_checker_thread, args=(window,), daemon=True)
                worker.start()

        if event == "-CANCELAR-":
            cancel_scheduled = True
            window["-CANCELAR-"].update(disabled=True)
            window["-STATUS_AGENDAMENTO-"].update("Cancelando...", text_color="orange")

        if event == "-APPEND_LOG-":
            window["-LOG-"].write(values["-APPEND_LOG-"])

        if event == "-UPDATE_STATUS-":
            window["-STATUS_AGENDAMENTO-"].update(values["-UPDATE_STATUS-"], text_color="blue")

        if event == "-SCHEDULE_COMPLETE-":
            window["-CANCELAR-"].update(disabled=True)
            window["-STATUS_AGENDAMENTO-"].update("Execução concluída", text_color="green")

        if event == "-SCHEDULE_CANCELLED-":
            window["-CANCELAR-"].update(disabled=True)
            window["-STATUS_AGENDAMENTO-"].update("Agendamento cancelado", text_color="gray")

        if event == "-SCHEDULE_ERROR-":
            window["-CANCELAR-"].update(disabled=True)
            window["-STATUS_AGENDAMENTO-"].update("Erro no agendamento", text_color="red")

    window.close()

if __name__ == "__main__":
    main()
