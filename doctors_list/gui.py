"""Tk desktop application. Starts read-only; network runs away from the UI thread."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from .cli import _config, _live
from .engine import evaluate
from .google_reader import ReadError, build_service
from .master import load_master, sync_master
from .storage import load_json, private_path, save_private_json
from .workflow import backend, state_directory
from . import writer

STATUS_LABELS = {'eligible': '登録候補', 'duplicate': '重複', 'excluded': '除外', 'hold': '保留'}


def summary_text(report):
    before, after = report['before'], report['after']
    people = lambda x: '不明（本人同定未完了）' if x is None else str(x)
    counts = report['candidate_counts']
    return (f"{'保存検証済み' if report['mode'] == 'applied' else 'Dry Run・未書き込み'}\n"
            f"登録行数: {before['registered_rows']} → {after['registered_rows']}　"
            f"実人数: {people(before['actual_people'])} → {people(after['actual_people'])}\n"
            f"候補: 登録候補{counts['eligible']} / 重複{counts['duplicate']} / 除外{counts['excluded']} / 保留{counts['hold']}\n"
            f"実際の追加: 医師{report['newly_added']['doctor']} / 歯科医師{report['newly_added']['dentist']}　"
            f"最終行: {after['last_data_row']}\n保存後検証: {report['post_save_verification']}\n"
            f"正本: {report['canonical_url']}")


class App:
    def __init__(self, root):
        self.root = root
        root.title('ReBIRTH 医師リスト管理')
        root.geometry('1120x820')
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.events = queue.Queue()
        self.busy = False
        self.plan = None
        self.last_plan_id = None
        self.report = None
        self.records = {}
        self.candidate_records = {}
        self.closed = False
        self.buttons = []
        folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ReBIRTH' / 'DoctorsList'
        self.preferences_path = folder / 'gui-preferences.json'
        preferences = {}
        if self.preferences_path.exists():
            try:
                preferences = load_json(self.preferences_path)
                if not isinstance(preferences, dict):
                    preferences = {}
            except Exception:
                messagebox.showwarning('設定の確認', '前回の選択を読めません。最新の除外マスターを選び直してください。', parent=root)
        self.config_path = tk.StringVar(value=str(folder / 'settings.json'))
        self.master_path = tk.StringVar(value=preferences.get('master_path', str(folder / 'master-updated.json')))
        self.candidates_path = tk.StringVar()
        self.single_writer = tk.BooleanVar(value=False)
        self.real_reviewed = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='読み取り専用で開始します。候補JSONと除外マスターを選び、Dry Runを実行してください。')
        self.summary = tk.StringVar(value='本番への書き込みは、差分準備・専用認証・明示承認の後にだけ実行します。')
        main = ttk.Frame(root, padding=12); main.pack(fill='both', expand=True)
        for caption, var in [('設定', self.config_path), ('候補JSON', self.candidates_path), ('除外マスター', self.master_path)]:
            line = ttk.Frame(main); line.pack(fill='x', pady=3)
            ttk.Label(line, text=caption, width=13).pack(side='left')
            ttk.Entry(line, textvariable=var, state='readonly').pack(side='left', fill='x', expand=True)
            self.button(line, '選択', lambda v=var: self.choose(v)).pack(side='left', padx=6)
            var.trace_add('write', self.invalidate)
        toolbar = ttk.Frame(main); toolbar.pack(fill='x', pady=8)
        self.button(toolbar, '1. Dry Run', self.dry_run).pack(side='left', padx=3)
        self.button(toolbar, 'マスター更新', self.sync).pack(side='left', padx=3)
        self.button(toolbar, '2. 追記する差分を準備', self.prepare).pack(side='left', padx=3)
        self.button(toolbar, '保存状態を確認', self.recover).pack(side='left', padx=3)
        tabs = ttk.Notebook(main); tabs.pack(fill='both', expand=True)
        results = ttk.Frame(tabs); tabs.add(results, text='判定一覧')
        self.table = ttk.Treeview(results, columns=('status', 'name', 'reason'), show='headings')
        for field, text, width in [('status', '判定', 90), ('name', '氏名', 160), ('reason', '理由・一致先（ダブルクリックで詳細）', 650)]:
            self.table.heading(field, text=text); self.table.column(field, width=width)
        self.table.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(results, orient='vertical', command=self.table.yview)
        scroll.pack(side='right', fill='y'); self.table.configure(yscrollcommand=scroll.set)
        self.table.bind('<Double-1>', self.detail)
        diff_frame = ttk.Frame(tabs); tabs.add(diff_frame, text='追記差分・A〜G')
        self.diff = tk.Text(diff_frame, wrap='word', state='disabled')
        self.diff.pack(side='left', fill='both', expand=True)
        diff_scroll = ttk.Scrollbar(diff_frame, command=self.diff.yview)
        diff_scroll.pack(side='right', fill='y'); self.diff.configure(yscrollcommand=diff_scroll.set)
        ttk.Label(main, textvariable=self.summary, wraplength=1060, justify='left').pack(fill='x', pady=8)
        self.checks = []
        for text, variable in [('今回の根拠・判定・追記差分を確認済み（架空データではない）', self.real_reviewed),
                               ('他PC・他担当者・手動編集を停止し、このPCだけで書き込む', self.single_writer)]:
            check = ttk.Checkbutton(main, text=text, variable=variable, command=self.refresh_buttons)
            check.pack(anchor='w'); self.checks.append(check)
        actions = ttk.Frame(main); actions.pack(fill='x', pady=8)
        self.button(actions, '読み取り認証', lambda: self.auth(False)).pack(side='left', padx=3)
        self.button(actions, '書き込み用の認証を準備', lambda: self.auth(True)).pack(side='left', padx=3)
        self.apply_button = self.button(actions, '3. 差分を承認して追記', self.apply)
        self.apply_button.pack(side='right', padx=3)
        ttk.Label(main, textvariable=self.status, wraplength=1060, foreground='#244a68').pack(fill='x')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)
        self.refresh_buttons()

    def button(self, frame, text, command):
        button = ttk.Button(frame, text=text, command=lambda: self.guard(command))
        self.buttons.append(button)
        return button

    def guard(self, action):
        if self.busy:
            return
        try:
            action()
        except (ValueError, ReadError, writer.WriteStopped) as e:
            messagebox.showerror('確認が必要です', str(e), parent=self.root)
        except Exception:
            messagebox.showerror('安全停止', '入力ファイル・設定・保存先を確認してください。認証情報やデータの内容は表示しません。', parent=self.root)

    def choose(self, variable):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[('JSON', '*.json')])
        if path:
            variable.set(path)
            if variable is self.master_path:
                self.remember_master()

    def remember_master(self):
        save_private_json(self.preferences_path, {'master_path': self.master_path.get()})

    def invalidate(self, *_):
        self.plan = None
        self.real_reviewed.set(False); self.single_writer.set(False)
        if hasattr(self, 'apply_button'):
            self.refresh_buttons()

    def refresh_buttons(self):
        for button in self.buttons:
            button.configure(state='disabled' if self.busy else 'normal')
        for check in getattr(self, 'checks', []):
            check.configure(state='disabled' if self.busy else 'normal')
        if hasattr(self, 'apply_button'):
            allowed = self.plan is not None and self.real_reviewed.get() and self.single_writer.get() and not self.busy
            self.apply_button.configure(state='normal' if allowed else 'disabled')

    def inputs(self):
        config = _config(self.config_path.get())
        candidates = load_json(private_path(self.candidates_path.get()))
        master = load_master(private_path(self.master_path.get()))
        self.candidate_records = {c['id']: c for c in candidates}
        return config, candidates, master

    def run(self, label, work, on_success):
        self.busy = True; self.status.set(label); self.refresh_buttons()
        future = self.pool.submit(work)
        future.add_done_callback(lambda f: self.events.put((f, on_success)))

    def poll(self):
        try:
            future, done = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            try:
                done(future.result())
            except (ReadError, writer.WriteStopped) as e:
                self.status.set(str(e)); messagebox.showerror('確認が必要です', str(e), parent=self.root)
            except Exception:
                text = '処理を停止しました。入力・認証・保存先を確認してください。追記後のエラーの場合は「保存状態を確認」を使い、再追記しないでください。'
                self.status.set(text); messagebox.showerror('安全停止', text, parent=self.root)
            self.refresh_buttons()
        if not self.closed:
            self.root.after(100, self.poll)

    def display(self, report):
        self.report = report
        self.records = {}
        self.table.delete(*self.table.get_children())
        for index, entry in enumerate(report['assessments']):
            reasons = entry['reasons'] + [f"{m['scope']}: {m['target']} ({m['level']})" for m in entry['matches']]
            iid = str(index); self.records[iid] = entry
            self.table.insert('', 'end', iid=iid, values=(STATUS_LABELS[entry['status']], entry['name'], ' / '.join(reasons)))
        self.summary.set(summary_text(report))
        lines = ['A:追加日 / B:配信状況 / C:氏名 / D:勤務先＆役職 / E:メール / F:出典 / G:備考',
                 '既存行・列幅は変更せず、追加行だけ高さ20px、見本行の書式・入力規則を継承します。']
        lines.extend(f"確認シート: {s['title']} / 分類: {s['kind']} / 登録行数: {s['registered_rows']} / 最終行: {s['last_data_row']}" for s in report['sheet_inventory'])
        lines.extend('停止理由: ' + reason for reason in report['blockers'])
        change = report['proposed_change']
        for i, values in enumerate(change['rows_A_G'], change['start_row']):
            lines.append(f'\n追加予定の行 {i}')
            lines.extend(f'{column}: {value or "（空欄）"}' for column, value in zip('ABCDEFG', values))
        self.diff.configure(state='normal'); self.diff.delete('1.0', 'end')
        self.diff.insert('1.0', '\n'.join(lines)); self.diff.configure(state='disabled')

    def detail(self, _event):
        selected = self.table.selection()
        if not selected:
            return
        dialog = tk.Toplevel(self.root); dialog.title('判定の詳細'); dialog.geometry('800x520')
        text = tk.Text(dialog, wrap='word'); text.pack(fill='both', expand=True)
        assessment = self.records[selected[0]]
        text.insert('1.0', json.dumps({'assessment': assessment, 'candidate': self.candidate_records.get(assessment['id'])}, ensure_ascii=False, indent=2)); text.configure(state='disabled')

    def dry_run(self):
        self.invalidate()
        config, candidates, master = self.inputs()
        def work():
            report = evaluate(_live(config), candidates, master)
            save_private_json(Path(config['token_path']).parent / 'report-latest.json', report)
            return report
        def done(report):
            self.display(report); self.status.set('Dry Run完了。Googleシートには書き込んでいません。')
        self.run('正本を取得して候補を照合しています…', work, done)

    def sync(self):
        self.invalidate()
        config = _config(self.config_path.get())
        old_path = private_path(self.master_path.get()); old = load_master(old_path)
        target = Path(config['token_path']).parent / ('master-' + writer.utcnow().strftime('%Y%m%d-%H%M%S-%f') + '.json')
        def done(_):
            self.master_path.set(str(target)); self.status.set('最新の正本でマスター更新・保存確認が完了しました。元ファイルは保持しています。')
            self.remember_master()
        self.run('全シートとマスターを照合しています…', lambda: sync_master(_live(config), target, old), done)

    def prepare(self):
        self.invalidate()
        config, candidates, master = self.inputs()
        def work():
            plan = writer.prepare(backend(config), candidates, master)
            save_private_json(Path(config['token_path']).parent / 'plans' / (plan['plan_id'] + '.json'), plan)
            return plan
        def done(plan):
            self.plan = plan; self.last_plan_id = plan['plan_id']
            self.display(plan['payload']['report'])
            self.status.set('承認対象を準備しました。判定一覧・差分を確認してください。準備は15分有効です。操作ID: ' + plan['plan_id'])
        self.run('最新の正本・書式を取得し、承認対象を準備しています…', work, done)

    def auth(self, writable):
        config = _config(self.config_path.get())
        if writable and not messagebox.askyesno('書き込み認証', 'Googleのスプレッドシート編集権限を、読み取り用と別のトークンへ保存します。認証だけでは追記しません。続けますか？', parent=self.root):
            return
        work = (lambda: backend(config, writable=True, interactive=True)) if writable else (lambda: build_service(config['client_secrets_path'], config['token_path'], interactive=True))
        self.run('ブラウザでGoogleの認証を完了してください…', work, lambda _: self.status.set('認証完了。Googleシートは変更していません。'))

    def apply(self):
        if not self.plan or not self.real_reviewed.get() or not self.single_writer.get():
            return
        config = _config(self.config_path.get()); plan = deepcopy(self.plan)
        phrase = writer.approval_phrase(plan)
        entered = simpledialog.askstring('本番追記の明示承認', f"この差分の{len(plan['payload']['spec']['rows'])}名を正本へ追記します。\n承認する場合だけ、次の文字を入力してください。\n\n{phrase}", parent=self.root)
        if entered != phrase:
            self.status.set('承認されていないため追記していません。'); return
        self.last_plan_id = plan['plan_id']; self.plan = None
        def done(report):
            self.display(report)
            self.master_path.set(str(state_directory(config) / plan['plan_id'] / 'master-updated.json'))
            self.remember_master()
            self.status.set('保存後検証・マスター保存が完了しました。操作ID: ' + plan['plan_id'])
        self.run('追記直前の再確認・バックアップ・保存検証を実行しています。画面を閉じないでください…',
                 lambda: writer.apply(backend(config, writable=True), plan, state_directory(config), entered, True), done)

    def recover(self):
        config = _config(self.config_path.get()); state = state_directory(config)
        plan_id = self.last_plan_id
        if not plan_id:
            file = filedialog.askopenfilename(parent=self.root, title='確認する操作のjournal.jsonを選択', initialdir=str(state), filetypes=[('操作記録', 'journal.json')])
            if not file:
                return
            record = load_json(file); plan_id = record['plan_id']
            if Path(file).resolve() != (state / plan_id / 'journal.json').resolve():
                raise ValueError('この環境の操作記録を選択してください。')
        self.plan = None
        def done(report):
            self.display(report)
            self.master_path.set(str(state / plan_id / 'master-updated.json'))
            self.remember_master()
            self.status.set('正本を再取得して保存状態を確認しました。Googleへの再送はしていません。')
        self.run('保存状態を読み取りで照合しています…', lambda: writer.recover(backend(config), state, plan_id), done)

    def close(self):
        if self.busy:
            messagebox.showinfo('処理中', '保存状態の確認のため、処理が終わるまでお待ちください。', parent=self.root)
            return
        self.closed = True; self.pool.shutdown(wait=False); self.root.destroy()


def main():
    root = tk.Tk(); App(root); root.mainloop()
    return 0


if __name__ == '__main__':
    main()
