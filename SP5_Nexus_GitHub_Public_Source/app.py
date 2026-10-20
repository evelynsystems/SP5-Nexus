#!/usr/bin/env python3
"""SP5 Nexus: a PS5 inspired Linux graphical frontend powered by AnyPS5."""
from __future__ import annotations
import os, queue, subprocess, sys, threading, tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import backend as B
import engine_manager as E

DARK='#071223'; DARK2='#101e32'; BLUE='#176dff'; LIGHT='#ecf2fc'; WHITE='#ffffff'; MUTED='#8496b1'; GREEN='#17d89b'

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SP5 Nexus  |  Powered by AnyPS5')
        self.geometry('1450x890'); self.minsize(1070,690)
        self.configure(bg=DARK)
        self.settings = B.defaults() | B.load_json(B.CONFIG_FILE,{})
        self.games = B.load_json(B.GAMES_FILE, [])
        self.events=queue.Queue(); self.proc=None; self.selected=None
        self.search=tk.StringVar(); self.search.trace_add('write',lambda *_:self.update_library())
        self.page='Library'
        self.sidebar=tk.Frame(self,bg=DARK,width=235);self.sidebar.pack(side='left',fill='y');self.sidebar.pack_propagate(False)
        self.content=tk.Frame(self,bg=LIGHT);self.content.pack(side='left',fill='both',expand=True)
        self.nav_widgets={}
        tk.Label(self.sidebar,text='SP5',fg=WHITE,bg=DARK,font=('DejaVu Sans',24,'bold')).place(x=25,y=30)
        tk.Label(self.sidebar,text='NEXUS',fg='#368dff',bg=DARK,font=('DejaVu Sans',24,'bold')).place(x=92,y=30)
        tk.Label(self.sidebar,text='Powered by AnyPS5',fg='#b6cfff',bg=DARK,font=('DejaVu Sans',10,'bold')).place(x=27,y=75)
        tk.Label(self.sidebar,text='YOUR GAMES. YOUR SYSTEM.',fg=MUTED,bg=DARK,font=('DejaVu Sans',8)).place(x=27,y=98)
        for idx,(item,icon) in enumerate([('Library','▦'),('Engine Manager','⬡'),('Convert Game','⟳'),('Launch','▶'),('Diagnostics','◉'),('Settings','⚙'),('Logs','≡'),('About','ⓘ')]):
            b=tk.Button(self.sidebar,text=f'{icon}     {item}',font=('DejaVu Sans',12),anchor='w',bd=0,relief='flat',cursor='hand2',padx=18,pady=13,command=lambda p=item:self.show(p))
            b.place(x=14,y=137+idx*54,width=205,height=48);self.nav_widgets[item]=b
        tk.Label(self.sidebar,text='PS5 INSPIRED  •  LINUX',bg=DARK,fg='#6782ad',font=('DejaVu Sans',9)).place(x=25,rely=.94)
        self.show('Library');self.after(120,self.poll)
        B.log('SP5 Nexus opened; powered by AnyPS5')

    def button(self,parent,text,action,primary=True,width=None):
        return tk.Button(parent,text=text,command=action,fg=WHITE if primary else DARK,bg=BLUE if primary else '#dbe5f5',activebackground='#0958da' if primary else '#ccd9ed',activeforeground=WHITE if primary else DARK,bd=0,relief='flat',cursor='hand2',font=('DejaVu Sans',10,'bold'),pady=10,width=width)

    def panel(self,parent,bg=WHITE,pad=15):
        f=tk.Frame(parent,bg=bg,highlightthickness=1,highlightbackground='#d8e2f0');f.pack(fill='x',pady=9);return f

    def label(self,parent,text,size=11,bold=False,color=DARK,bg=None,**kw):
        return tk.Label(parent,text=text,font=('DejaVu Sans',size,'bold' if bold else 'normal'),bg=bg or parent.cget('bg'),fg=color,**kw)

    def show(self,page):
        self.page=page
        for name,b in self.nav_widgets.items():b.configure(bg='#164894' if name==page else DARK,fg=WHITE if name==page else '#b8c7df')
        for child in self.content.winfo_children():child.destroy()
        head=tk.Frame(self.content,bg=LIGHT);head.pack(fill='x',padx=24,pady=(18,10))
        self.label(head,'SP5 Nexus  /  '+page,20,True).pack(side='left')
        self.label(head,'LINUX EDITION',9,True,'#4875ae').pack(side='right')
        body=tk.Frame(self.content,bg=LIGHT);body.pack(fill='both',expand=True,padx=25,pady=(0,15))
        getattr(self,'page_'+page.lower().replace(' ','_'))(body)

    def page_library(self,parent):
        hero=tk.Frame(parent,bg=DARK2,height=175);hero.pack(fill='x');hero.pack_propagate(False)
        art=Path(__file__).with_name('hero.png')
        if art.is_file():
            self.hero_image=tk.PhotoImage(file=str(art))
            tk.Label(hero,image=self.hero_image,bg=DARK2,bd=0).place(relx=1.0,x=-490,y=0,width=490,height=175)
        self.label(hero,'SP5 NEXUS  •  POWERED BY ANYPS5',10,True,'#70aafa',DARK2).pack(anchor='w',padx=25,pady=(22,4))
        self.label(hero,'Play more. Anywhere.',26,True,WHITE,DARK2).pack(anchor='w',padx=25)
        self.label(hero,'Import your own ELF games, convert with AnyPS5, then launch on Linux.',10,False,'#c2d0e5',DARK2).pack(anchor='w',padx=25,pady=8)
        self.button(hero,'+  ADD GAME',self.add_game).place(x=25,y=128,width=170,height=36)
        top=tk.Frame(parent,bg=LIGHT);top.pack(fill='x',pady=(17,4))
        self.label(top,'Your Library',18,True).pack(side='left')
        tk.Entry(top,textvariable=self.search,font=('DejaVu Sans',11),relief='flat',bg=WHITE,fg=DARK,insertbackground=DARK).pack(side='right',ipady=9,ipadx=8)
        self.cards=tk.Frame(parent,bg=LIGHT);self.cards.pack(fill='x',pady=(8,8))
        self.details=self.panel(parent);self.details.configure(height=165);self.details.pack_propagate(False)
        self.update_library()
        right=tk.Frame(parent,bg=LIGHT);right.pack(fill='x')
        self.button(right,'RUN GPU DIAGNOSTICS',lambda:self.show('Diagnostics'),False).pack(side='left',padx=(0,9))
        self.button(right,'OPEN CONVERTER',lambda:self.show('Convert Game')).pack(side='left')

    def update_library(self):
        if self.page!='Library' or not hasattr(self,'cards'):return
        for w in self.cards.winfo_children():w.destroy()
        displayed=[g for g in self.games if self.search.get().lower() in g['name'].lower()]
        if not displayed:
            self.label(self.cards,'No games yet. Click ADD GAME to import a plain ELF executable.',12,color='#53657d').pack(anchor='w',pady=35)
        for i,g in enumerate(displayed[:12]):
            f=tk.Frame(self.cards,bg=DARK2,width=145,height=135,highlightbackground='#4598ff' if self.selected==g['id'] else '#9fafd0',highlightthickness=2)
            f.grid(row=i//6,column=i%6,padx=6,pady=6,sticky='nsew');f.grid_propagate(False)
            self.label(f,'◈',27,True,'#4fa4ff',DARK2).pack(pady=(14,4))
            self.label(f,g['name'][:19],10,True,WHITE,DARK2,wraplength=130).pack()
            self.label(f,'CONVERTED' if g.get('output') and Path(g['output']).exists() else 'SOURCE ELF',8,color='#70d9af' if g.get('output') else '#a1b2ca',bg=DARK2).pack(pady=6)
            for w in (f,*f.winfo_children()):w.bind('<Button-1>',lambda e,game=g:self.select_game(game))
        self.update_details()

    def update_details(self):
        if not hasattr(self,'details'):return
        for w in self.details.winfo_children():w.destroy()
        game=next((g for g in self.games if g['id']==self.selected),None)
        if not game and self.games:game=self.games[0];self.selected=game['id']
        if not game:
            self.label(self.details,'Select a game to view its actual conversion and launch status.',12,color='#526481').pack(anchor='w',padx=20,pady=25);return
        left=tk.Frame(self.details,bg=WHITE);left.pack(side='left',fill='both',expand=True,padx=20,pady=14)
        self.label(left,game['name'],18,True).pack(anchor='w')
        state='Converted output found' if game.get('output') and Path(game['output']).is_file() else 'Not converted'
        self.label(left,state,11,color='#087d58' if 'found' in state else '#ac6c21').pack(anchor='w',pady=4)
        self.label(left,'Source: '+game['elf'],9,color='#526481',wraplength=650,justify='left').pack(anchor='w')
        actions=tk.Frame(self.details,bg=WHITE);actions.pack(side='right',padx=20,pady=12)
        self.button(actions,'⟳  CONVERT',lambda:self.convert_selected()).pack(fill='x',pady=4)
        self.button(actions,'▶  LAUNCH',lambda:self.launch_selected(),False).pack(fill='x',pady=4)
        self.button(actions,'REMOVE FROM LIBRARY',self.remove_selected,False).pack(fill='x',pady=4)

    def select_game(self,game):self.selected=game['id'];self.update_library()
    def add_game(self):
        p=filedialog.askopenfilename(title='Choose unencrypted plain ELF executable',filetypes=[('All files','*')])
        if not p:return
        try:B.validate_elf(p)
        except ValueError as e:messagebox.showerror('Unsupported input',str(e));return
        from uuid import uuid4
        g={'id':uuid4().hex,'name':Path(p).stem,'elf':str(Path(p).resolve()),'output':'','module_parent':'','resources':''}
        self.games.append(g);self.selected=g['id'];B.save_json(B.GAMES_FILE,self.games);self.notice('Imported '+g['name']);self.show('Library')

    def remove_selected(self):
        game=self.current_game()
        if game and messagebox.askyesno('Remove from library','Remove entry only? No game files will be deleted.'):
            self.games=[x for x in self.games if x['id']!=game['id']];self.selected=None;B.save_json(B.GAMES_FILE,self.games);self.show('Library')

    def current_game(self):return next((g for g in self.games if g['id']==self.selected),None)

    def page_convert_game(self,parent):
        self.label(parent,'Convert a game',21,True).pack(anchor='w',pady=12)
        self.label(parent,'Relinker converts clean ELF executables. Source modules are required.',11,color='#58708c').pack(anchor='w')
        self.game_picker(parent)
        self.label(parent,'Optional: module parent directory (contains sce_module, sce_modules, or prx)',11).pack(anchor='w',pady=(18,5))
        self.modvar=tk.StringVar(value=self.current_game().get('module_parent','') if self.current_game() else '')
        self.path_row(parent,self.modvar,True)
        self.label(parent,'Optional: app0 resources directory (copied only when staging runtime)',11).pack(anchor='w',pady=(15,5))
        self.resvar=tk.StringVar(value=self.current_game().get('resources','') if self.current_game() else '')
        self.path_row(parent,self.resvar,True)
        self.button(parent,'⟳  CONVERT SELECTED GAME',self.convert_selected).pack(anchor='w',pady=18)
        self.label(parent,'Conversion does not guarantee the game will run. No game files are included.',10,color='#60738d').pack(anchor='w')

    def game_picker(self,parent):
        self.label(parent,'Selected game',11,True).pack(anchor='w',pady=(20,5))
        choices=[g['name']+'  ['+g['id'][:6]+']' for g in self.games]
        value=next((i for i,g in enumerate(self.games) if g['id']==self.selected),0)
        box=ttk.Combobox(parent,values=choices,state='readonly',width=49)
        if choices:box.current(value)
        box.pack(anchor='w',ipady=6)
        def changed(_):self.selected=self.games[box.current()]['id']
        box.bind('<<ComboboxSelected>>',changed)
        self.button(parent,'IMPORT GAME',self.add_game,False).pack(anchor='w',pady=10)

    def path_row(self,parent,var,directory=False):
        line=tk.Frame(parent,bg=LIGHT);line.pack(fill='x')
        tk.Entry(line,textvariable=var,relief='flat',font=('DejaVu Sans',11),bg=WHITE).pack(side='left',fill='x',expand=True,ipady=10)
        def browse():
            p=filedialog.askdirectory() if directory else filedialog.askopenfilename()
            if p:var.set(p)
        self.button(line,'BROWSE',browse,False).pack(side='left',padx=8)

    def page_launch(self,parent):
        self.label(parent,'Launch converted games',21,True).pack(anchor='w',pady=13)
        self.game_picker(parent)
        self.label(parent,'Launch uses the native Linux executable generated by relinker.',11,color='#526784').pack(anchor='w',pady=10)
        self.button(parent,'▶  LAUNCH SELECTED GAME',self.launch_selected).pack(anchor='w',pady=10)
        self.button(parent,'OPEN GAME OUTPUT FOLDER',self.open_output_folder,False).pack(anchor='w',pady=8)
        self.label(parent,'Games are experimental. Check logs for actual failures.',11,color='#62758d').pack(anchor='w',pady=20)

    def page_diagnostics(self,parent):
        self.label(parent,'System diagnostics',22,True).pack(anchor='w',pady=14)
        self.label(parent,'AMD, NVIDIA and Vulkan inspection for your Linux PC',12,color='#506884').pack(anchor='w')
        self.diag=tk.Text(parent,height=19,wrap='word',bg=DARK2,fg='#b9dbff',insertbackground=WHITE,font=('DejaVu Sans Mono',10),relief='flat',padx=20,pady=15)
        self.diag.pack(fill='both',expand=True,pady=15)
        self.button(parent,'◉  RUN DIAGNOSTICS',self.run_diagnostics).pack(anchor='w')
        self.run_diagnostics()

    def page_engine_manager(self,parent):
        self.label(parent,'AnyPS5 Engine Manager',22,True).pack(anchor='w',pady=12)
        self.label(parent,'Official upstream source, compiled locally for Ubuntu.',12,
                   color='#526784').pack(anchor='w')
        self.label(parent,'Download official AnyPS5 source into Studio, build its relinker and PRX runtime libraries.',
                   10,color='#526784').pack(anchor='w',pady=8)
        self.engine_source=tk.StringVar(value=str(E.DEFAULT_SOURCE))
        self.label(parent,'Engine source directory',11,True).pack(anchor='w',pady=(12,5))
        self.path_row(parent,self.engine_source,True)
        self.engine_status=self.label(parent,'Checking engine...',11,True)
        self.engine_status.pack(anchor='w',pady=13)
        bar=tk.Frame(parent,bg=LIGHT);bar.pack(fill='x')
        self.button(bar,'CHECK ENGINE',self.check_engine).pack(side='left',padx=(0,8))
        self.button(bar,'INSTALL OR BUILD',lambda:self.install_engine(False)).pack(side='left',padx=8)
        self.button(bar,'UPDATE AND REBUILD',lambda:self.install_engine(True),False).pack(side='left',padx=8)
        self.label(parent,'Required Ubuntu dependencies:',10,True).pack(anchor='w',pady=(16,5))
        cmd='sudo apt update && sudo apt install '+ ' '.join(E.PACKAGES)
        entry=tk.Entry(parent,font=('DejaVu Sans Mono',9),relief='flat')
        entry.insert(0,cmd);entry.configure(state='readonly');entry.pack(fill='x',ipady=10)
        self.button(parent,'COPY INSTALL COMMAND',lambda:self.copy_to_clipboard(cmd),False).pack(anchor='w',pady=8)
        self.engine_log=tk.Text(parent,height=12,bg=DARK2,fg='#b9dbff',wrap='word',
                                font=('DejaVu Sans Mono',9),relief='flat')
        self.engine_log.pack(fill='both',expand=True,pady=10)
        self.check_engine()

    def copy_to_clipboard(self,content):
        self.clipboard_clear();self.clipboard_append(content)
        self.notice('Copied prerequisites command')

    def check_engine(self):
        if self.page!='Engine Manager':return
        s=E.status(self.engine_source.get())
        verdict='ENGINE READY' if s['relinker_ready'] and s['libs_count'] else 'ENGINE NOT COMPLETE'
        self.engine_status.configure(text=f"{verdict}   |   Relinker: {s['relinker_ready']}   |   PRX libraries: {s['libs_count']}")
        self.engine_log.insert('end',f"\n{verdict}\nSource: {s['source']}\nRelinker: {s['relinker']}\nLibraries: {s['libs']}\n")
        self.engine_log.see('end')
        if s['relinker_ready'] and s['libs_count']:
            self.settings['relinker']=s['relinker'];self.settings['libs']=s['libs']
            B.save_json(B.CONFIG_FILE,self.settings)

    def install_engine(self,update):
        if getattr(self,'engine_busy',False):
            messagebox.showwarning('Engine busy','Engine installation already running.');return
        source=self.engine_source.get().strip()
        if not messagebox.askyesno('Install AnyPS5 engine',
            'Download and compile the official AnyPS5 engine into:\n'+source+
            '\n\nThis requires internet access and installed Ubuntu build dependencies. Continue?'):
            return
        self.engine_busy=True
        self.engine_log.insert('end','\nBeginning engine installation...\n')
        def work():
            try:
                result=E.install(lambda line:self.events.put(('engine_line',line)),source,update)
                self.events.put(('engine_done',result))
            except Exception as exc:
                self.events.put(('engine_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def page_settings(self,parent):
        self.label(parent,'Application settings',22,True).pack(anchor='w',pady=14)
        self.vars={}
        for key,title,directory in [('relinker','AnyPS5 relinker binary',False),('libs','Built AnyPS5 runtime library folder',True),('output_dir','Converted games destination',True),('gpu','Vulkan GPU name preference (blank = automatic)',None)]:
            self.label(parent,title,11,True).pack(anchor='w',pady=(14,5))
            v=tk.StringVar(value=str(self.settings.get(key,'')));self.vars[key]=v
            if directory is None:tk.Entry(parent,textvariable=v,font=('DejaVu Sans',11),relief='flat').pack(fill='x',ipady=9)
            else:self.path_row(parent,v,directory)
        self.intel_var=tk.BooleanVar(value=bool(self.settings.get('intel',False)))
        tk.Checkbutton(parent,text='Convert unsupported AMD instructions for an Intel host',variable=self.intel_var,bg=LIGHT,fg=DARK,font=('DejaVu Sans',10)).pack(anchor='w',pady=16)
        self.button(parent,'SAVE SETTINGS',self.save_settings).pack(anchor='w',pady=8)

    def save_settings(self):
        for k,v in self.vars.items():self.settings[k]=v.get().strip()
        self.settings['intel']=self.intel_var.get()
        B.save_json(B.CONFIG_FILE,self.settings);self.notice('Settings saved')

    def page_about(self,parent):
        self.label(parent,'SP5 Nexus',26,True).pack(anchor='w',pady=(18,3))
        self.label(parent,'Powered by AnyPS5',14,True,'#176dff').pack(anchor='w')
        self.label(parent,'Your Games. Your System.',11,color='#526784').pack(anchor='w',pady=(8,22))
        self.label(parent,'Linux game library and graphical frontend for the experimental AnyPS5 engine.',12,
                   color='#526784',wraplength=750,justify='left').pack(anchor='w',pady=8)
        self.label(parent,'Engine credit: boykopovar / AnyPS5',12,True).pack(anchor='w',pady=(20,6))
        self.label(parent,'Official source: https://github.com/boykopovar/AnyPS5',11,
                   color='#176dff').pack(anchor='w')
        self.label(parent,'The engine is an independent project. SP5 Nexus does not ship games, firmware, or decrypted assets.',
                   10,color='#526784',wraplength=760,justify='left').pack(anchor='w',pady=16)
        self.button(parent,'OPEN ANYPS5 SOURCE',lambda:subprocess.Popen(['xdg-open','https://github.com/boykopovar/AnyPS5']),False).pack(anchor='w',pady=8)

    def page_logs(self,parent):
        self.label(parent,'Recent activity',22,True).pack(anchor='w',pady=12)
        self.logs=tk.Text(parent,bg=DARK2,fg='#b8d0ef',font=('DejaVu Sans Mono',10),wrap='word',relief='flat',padx=15,pady=14)
        self.logs.pack(fill='both',expand=True,pady=10)
        self.refresh_logs()
        self.button(parent,'REFRESH LOGS',self.refresh_logs).pack(anchor='w',pady=8)

    def refresh_logs(self):
        if not hasattr(self,'logs') or self.page!='Logs':return
        try:content=''.join(B.LOG_FILE.read_text(encoding='utf-8').splitlines(keepends=True)[-250:])
        except OSError:content='No activity yet.'
        self.logs.delete('1.0','end');self.logs.insert('end',content);self.logs.see('end')

    def run_diagnostics(self):
        if self.page!='Diagnostics':return
        self.diag.delete('1.0','end');self.diag.insert('end','Checking GPU and Vulkan drivers...\n')
        def work():self.events.put(('diagnostics',B.diagnose()))
        threading.Thread(target=work,daemon=True).start()

    def convert_selected(self):
        game=self.current_game()
        if not game:messagebox.showwarning('No game','Import a game first.');return
        if self.proc and self.proc.poll() is None:messagebox.showwarning('Busy','Another conversion or game is running.');return
        module_parent=self.modvar.get().strip() if self.page=='Convert Game' else game.get('module_parent','')
        resources=self.resvar.get().strip() if self.page=='Convert Game' else game.get('resources','')
        game['module_parent']=module_parent;game['resources']=resources
        outdir=Path(self.settings['output_dir']).expanduser() / game['id']
        output=outdir/'app.elf'
        try:
            args=B.conversion_command(self.settings['relinker'],game['elf'],output,intel=self.settings['intel'],module_parent=module_parent or None)
        except (ValueError,OSError) as err:messagebox.showerror('Cannot convert',str(err));return
        B.save_json(B.GAMES_FILE,self.games)
        if not messagebox.askyesno('Convert game',f'Convert {game["name"]}?\n\nOutput: {output}\n\nThis may overwrite a previous converted output.'):
            return
        outdir.mkdir(parents=True,exist_ok=True)
        self.notice('Starting conversion for '+game['name'])
        def done(returncode):
            if returncode==0 and output.is_file():
                game['output']=str(output);B.save_json(B.GAMES_FILE,self.games)
                self.notice('Conversion completed. Staging runtime libraries...')
                try:
                    count=B.prepare_runtime(output,self.settings['libs'],resources or None)
                    self.notice(f'Runtime staged: {count} libraries. Game not yet verified playable.')
                except (OSError,ValueError) as exc:
                    self.notice('Conversion succeeded, but runtime staging failed: '+str(exc))
                self.show('Library')
            else:self.notice(f'Conversion failed (exit {returncode}). View Logs for details.')
        self.start_process(args,done,cwd=Path(game['elf']).parent)

    def launch_selected(self):
        game=self.current_game()
        if not game or not game.get('output'):messagebox.showwarning('Not converted','Select a converted game first.');return
        try:args,cwd,env=B.launch_command(game['output'],self.settings.get('gpu',''))
        except ValueError as err:messagebox.showerror('Cannot launch',str(err));return
        if not (cwd/'libs').is_dir():messagebox.showerror('Runtime missing','Missing libs directory. Reconvert and stage your built runtime libraries.');return
        self.notice('Launching '+game['name'])
        self.start_process(args,lambda code:self.notice(f'{game["name"]} exited with code {code}'),cwd=cwd,env=env)

    def open_output_folder(self):
        game=self.current_game()
        if not game or not game.get('output'):return
        parent=Path(game['output']).parent
        if parent.is_dir():subprocess.Popen(['xdg-open',str(parent)])

    def start_process(self,args,callback,cwd=None,env=None):
        if self.proc and self.proc.poll() is None:messagebox.showwarning('Busy','Another process is already running.');return
        def worker():
            code=-1
            try:
                self.proc=subprocess.Popen(args,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
                for line in self.proc.stdout:
                    self.events.put(('line',line.rstrip()))
                code=self.proc.wait()
            except OSError as exc:self.events.put(('line','Process error: '+str(exc)))
            self.events.put(('completed',(callback,code)))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,payload=self.events.get_nowait()
                if kind=='line':B.log(payload)
                elif kind=='completed':fn,code=payload;fn(code)
                elif kind=='engine_line':
                    B.log(payload)
                    if self.page=='Engine Manager':
                        self.engine_log.insert('end',payload+'\n');self.engine_log.see('end')
                elif kind=='engine_error':
                    self.engine_busy=False;self.notice('Engine install failed: '+payload)
                    if self.page=='Engine Manager':
                        self.engine_log.insert('end','ERROR: '+payload+'\n')
                    messagebox.showerror('Engine build failed',payload)
                elif kind=='engine_done':
                    self.engine_busy=False
                    self.settings['relinker']=payload['relinker']
                    self.settings['libs']=payload['libs']
                    B.save_json(B.CONFIG_FILE,self.settings)
                    self.notice('Engine installed and selected automatically')
                    if self.page=='Engine Manager':self.check_engine()
                elif kind=='diagnostics':
                    d=payload
                    self.notice(f'Diagnostics: GPU {"OK" if d["gpu_ok"] else "Unavailable"}, Vulkan {"OK" if d["vulkan_ok"] else "Unavailable"}')
                    if self.page=='Diagnostics':
                        self.diag.delete('1.0','end')
                        self.diag.insert('end','GPU\n'+d['gpu']+'\n\nVULKAN\n'+d['vulkan'])
        except queue.Empty:pass
        self.after(120,self.poll)

    def notice(self,msg):B.log(msg)

if __name__=='__main__':
    try:App().mainloop()
    except tk.TclError as exc:
        print('A graphical Linux desktop session is required:',exc,file=sys.stderr);sys.exit(1)
