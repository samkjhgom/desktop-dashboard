"""macOS live desktop dashboard.
Python 3.10+, pip install -r requirements.txt
"""
import argparse
import ctypes
import datetime as dt
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont

import psutil

BG = '#060e14'
PANEL = '#0a161e'
PANEL2 = '#10232c'
LINE = '#263f4a'
CYAN = '#80b1c0'
WHITE = '#d8e5e9'
MUTED = '#7d9aa3'
ORANGE = '#f4ae6d'
GREEN = '#61e9bc'
COUNTRIES = {'JP': ('일본', 'https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja'),
             'KR': ('한국', 'https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko'),
             'US': ('미국', 'https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en')}
CACHE = Path.home() / '.desktop_monitor_fx_cache.json'
MAP_PATH = Path(__file__).with_name('world_countries.json')
PROFILE_CACHE = Path.home() / '.desktop_country_profile_cache.json'
ACCENT = '#e65456'


def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'LocalDesktop/1.1 (+desktop-local)'})
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.loads(response.read().decode('utf-8'))


def fetch_fx():
    # Frankfurter's published rates, not tick-by-tick live market pricing.
    data = fetch_json('https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW,JPY')
    rates = data['rates']
    krw,jpy=float(rates['KRW']),float(rates['JPY'])
    if not (krw>0 and jpy>0):raise ValueError('Invalid exchange rates')
    return krw,jpy,data.get('date', 'unknown')


def fetch_news(code):
    req = urllib.request.Request(COUNTRIES[code][1], headers={'User-Agent': 'Mozilla/5.0 LocalDesktop'})
    with urllib.request.urlopen(req, timeout=12) as response:
        root = ET.fromstring(response.read())
    articles = []
    for item in root.findall('./channel/item')[:12]:
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        published = (item.findtext('pubDate') or '').strip()
        if title and link:
            articles.append({'title': title, 'link': link, 'date': published})
    return articles


def keywords(title, code):
    """Heuristic snippets, not AI analysis. Never invent news facts."""
    clean = re.sub(r'\s+-\s+[^-]+$', '', title).strip()
    if code == 'US':
        stop = {'the','and','for','with','from','this','that','over','after','amid','what','will','are','has','have','new','says','say','into','about','more','its','their'}
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]{2,}|\d+(?:\.\d+)?%?", clean) if w.lower() not in stop]
        return ' · '.join(words[:5]) or clean[:55]
    if code == 'KR':
        words = [w.strip('.,:!"“”()[]') for w in clean.split() if len(w.strip()) >= 2]
        return ' · '.join(words[:5]) or clean[:50]
    # Japanese generally lacks whitespace; show a faithful excerpt instead of falsely claiming NLP.
    return clean[:38] + ('…' if len(clean) > 38 else '')


def fetch_country_profile(iso2):
    if not iso2:
        raise ValueError('국가 코드 정보가 없습니다')
    fields='name,capital,population,currencies,languages,timezones,flag'
    data=fetch_json('https://restcountries.com/v3.1/alpha/'+urllib.parse.quote(iso2)+'?fields='+fields)
    if isinstance(data,list):
        data=data[0]
    currencies=data.get('currencies') or {}
    return {
        'name': (data.get('name') or {}).get('common',''),
        'capital': ', '.join(data.get('capital') or []) or '정보 없음',
        'population': data.get('population'),
        'currencies': ', '.join(f'{key} ({val.get("name",key)})' for key,val in currencies.items()) or '정보 없음',
        'languages': ', '.join((data.get('languages') or {}).values()) or '정보 없음',
        'timezone': ', '.join((data.get('timezones') or [])[:2]) or '정보 없음',
        'flag': data.get('flag') or ''
    }


def workerw_attach(window):
    """Best-effort Win32 WorkerW embedding; unsupported configurations fall back."""
    if sys.platform != 'win32':
        return False
    try:
        user32 = ctypes.windll.user32
        progman = user32.FindWindowW('Progman', None)
        if not progman:
            return False
        result = ctypes.c_ulong()
        user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(result))
        worker = ctypes.c_void_p()
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _):
            if user32.FindWindowExW(hwnd, 0, 'SHELLDLL_DefView', None):
                possible = user32.FindWindowExW(0, hwnd, 'WorkerW', None)
                if possible:
                    worker.value = possible
            return True
        cb = WNDENUMPROC(callback)
        user32.EnumWindows(cb, 0)
        target = worker.value or progman
        hwnd = window.winfo_id()
        # Re-parenting a Tk window behind desktop icons is Windows-shell-dependent.
        if not user32.SetParent(hwnd, target):
            return False
        user32.SetWindowPos(hwnd, 0, 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1), 0x0040 | 0x0010)
        return True
    except Exception:
        return False



def macos_wallpaper_attach(window):
    """Best-effort macOS desktop-level NSWindow, interactive and behind desktop icons.

    Needs pyobjc-framework-Cocoa and pyobjc-framework-Quartz. macOS desktop and
    Spaces behavior varies with OS version, Stage Manager and multi-monitor setup.
    """
    if sys.platform != 'darwin':
        return False
    try:
        import AppKit
        import Quartz
        # Let Tk create and map its backing NSWindow first.
        window.update_idletasks()
        app = AppKit.NSApplication.sharedApplication()
        matches = [w for w in app.windows() if str(w.title()) == 'Desktop']
        if not matches:
            return False
        nswindow = matches[0]
        screen = nswindow.screen() or AppKit.NSScreen.mainScreen()
        rect = screen.frame()
        nswindow.setFrame_display_(rect, True)
        # One layer above wallpaper, below Finder desktop icons.
        level = Quartz.CGWindowLevelForKey(Quartz.kCGDesktopWindowLevelKey) + 1
        nswindow.setLevel_(int(level))
        nswindow.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary
        )
        nswindow.setIgnoresMouseEvents_(False)
        nswindow.orderFrontRegardless()
        return True
    except Exception as exc:
        print('macOS wallpaper mode unavailable; opening normal window:', exc, file=sys.stderr)
        return False


class Monitor:
    def __init__(self, root, wallpaper=False):
        self.root = root
        self.wallpaper_requested = wallpaper
        self.wallpaper_active = False
        self.root.title('Desktop')
        self.root.configure(bg=BG)
        self.root.geometry('1360x820')
        self.root.minsize(1024, 700)
        self.root.bind('<Escape>', lambda _: self.exit())
        self.root.bind('<F11>', lambda _: self.toggle_fullscreen())
        self.root.protocol('WM_DELETE_WINDOW', self.exit)
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', self.render)
        self.canvas.bind('<Button-3>', self.show_context)
        self.canvas.bind('<MouseWheel>', self.on_map_wheel)
        self.canvas.bind('<Button-4>', lambda e: self.on_map_wheel(e, 1))
        self.canvas.bind('<Button-5>', lambda e: self.on_map_wheel(e, -1))
        self.canvas.bind('<ButtonPress-2>', self.start_map_pan)
        self.canvas.bind('<B2-Motion>', self.drag_map_pan)
        self.canvas.bind('<Shift-ButtonPress-1>', self.start_map_pan)
        self.canvas.bind('<Shift-B1-Motion>', self.drag_map_pan)
        self.fx = None
        self.fx_error = ''
        self.fx_updated = None
        self.fx_loading = False
        self.fx_source = 'NETWORK'
        self.world_countries = json.loads(MAP_PATH.read_text(encoding='utf-8'))
        self.map_zoom = 1.0
        self.map_center = (0.0, 0.0)  # longitude, latitude
        self.map_bounds = None
        self.map_drag = None
        self.country_selected = None
        self.country_info = {}
        self.country_loading = False
        self.country_error = ''
        self.country_time = {}
        try:
            self.country_info.update(json.loads(PROFILE_CACHE.read_text(encoding='utf-8')))
        except (ValueError, OSError):
            pass
        self.news_fetched = {}
        self.news_requested = {}
        self.news = {}
        self.news_open = None
        self.news_page = 0
        self.news_error = ''
        self.news_loading = False
        self.cpu_hist = deque([0.0]*65, maxlen=65)
        self.ram_hist = deque([0.0]*65, maxlen=65)
        self.cpu = 0
        self.ram = 0
        self.battery = None
        self.clock = ''
        self.last_update = 0
        self.live = True
        self.font_cache = {}
        self.root.after(100, self.tick)
        self.root.after(500, self.load_fx)
        self.root.after(500, self.enable_wallpaper)
        self.root.after(1000 * 60 * 15, self.fx_timer)
        self.restore_fx_cache()

    def enable_wallpaper(self):
        if self.wallpaper_requested:
            if sys.platform == 'win32':
                self.wallpaper_active = workerw_attach(self.root)
            elif sys.platform == 'darwin':
                self.wallpaper_active = macos_wallpaper_attach(self.root)
            if not self.wallpaper_active:
                print('Wallpaper integration unavailable; normal desktop window is active.', file=sys.stderr)
            self.render()

    def font(self, size=12, bold=False):
        key=(size,bold)
        if key not in self.font_cache:
            self.font_cache[key]=tkfont.Font(family='Consolas' if sys.platform=='win32' else 'TkFixedFont', size=max(7,int(size)), weight='bold' if bold else 'normal')
        return self.font_cache[key]

    def rect(self, x,y,w,h,fill=PANEL,outline=LINE):
        self.canvas.create_rectangle(x,y,x+w,y+h,fill=fill,outline=outline,width=1)

    def text(self,x,y,s,size=11,color=WHITE,anchor='nw',bold=False,width=None):
        return self.canvas.create_text(x,y,text=s,fill=color,font=self.font(size,bold),anchor=anchor,width=width)

    def panel(self,x,y,w,h,title):
        self.rect(x,y,w,h)
        self.rect(x,y,w,31,PANEL2)
        self.text(x+12,y+8,title,10,CYAN,bold=True)
        return x+12,y+44,w-24,h-56

    def btn(self,x,y,w,label,callback,selected=False,height=36):
        self.rect(x,y,w,height,fill='#17566a' if selected else '#0e3242',outline=CYAN if selected else LINE)
        tag=f'btn_{x}_{y}_{len(self.canvas.find_all())}'
        self.text(x+w/2,y+height/2,label,11,WHITE if selected else CYAN,anchor='center',bold=selected)
        # bind both items via overlapping transparent hitbox
        obj=self.canvas.create_rectangle(x,y,x+w,y+height,fill='',outline='',width=0,tags=(tag,))
        self.canvas.tag_bind(obj,'<Button-1>',lambda e:callback())
        self.canvas.tag_bind(obj,'<Enter>',lambda e:self.canvas.configure(cursor='hand2'))
        self.canvas.tag_bind(obj,'<Leave>',lambda e:self.canvas.configure(cursor=''))

    def graph(self,x,y,w,h,data,color):
        self.canvas.create_rectangle(x,y,x+w,y+h,fill='#071c28',outline=LINE)
        for i in range(1,4):
            yy=y+h*i/4
            self.canvas.create_line(x,yy,x+w,yy,fill='#173c4b')
        data=list(data)
        if len(data)>1:
            pts=[]
            for i,v in enumerate(data):
                pts.extend((x+i*w/(len(data)-1), y+h-3-(h-6)*v/100))
            self.canvas.create_line(*pts,fill=color,width=2,smooth=True)

    def map_inside(self, px, py):
        if not self.map_bounds:
            return False
        x,y,w,h=self.map_bounds
        return x <= px <= x+w and y <= py <= y+h

    def map_point_to_geo(self, px, py):
        x,y,w,h=self.map_bounds
        zoom=self.map_zoom
        lon=self.map_center[0]+(px-(x+w/2))*360/(w*zoom)
        lat=self.map_center[1]-(py-(y+h/2))*168/(h*zoom)
        return lon,lat

    def limit_map_center(self):
        z=self.map_zoom
        max_lon=180*(1-1/z)
        max_lat=84*(1-1/z)
        lon,lat=self.map_center
        self.map_center=(max(-max_lon,min(max_lon,lon)),max(-max_lat,min(max_lat,lat)))

    def adjust_map_zoom(self, factor, anchor=None):
        if not self.map_bounds:
            return
        if anchor is None:
            x,y,w,h=self.map_bounds
            anchor=(x+w/2,y+h/2)
        before=self.map_point_to_geo(*anchor)
        new_zoom=max(1.0,min(8.0,self.map_zoom*factor))
        if abs(new_zoom-self.map_zoom)<1e-7:return
        self.map_zoom=new_zoom
        after=self.map_point_to_geo(*anchor)
        self.map_center=(self.map_center[0]+before[0]-after[0],self.map_center[1]+before[1]-after[1])
        self.limit_map_center()
        self.render()

    def on_map_wheel(self,event,direction=None):
        if not self.map_inside(event.x,event.y):return
        if direction is None:
            direction=1 if event.delta>0 else -1
        self.adjust_map_zoom(1.2 if direction>0 else 1/1.2,(event.x,event.y))

    def reset_map_zoom(self):
        self.map_zoom=1.0
        self.map_center=(0.0,0.0)
        self.render()

    def start_map_pan(self,event):
        self.map_drag=(event.x,event.y) if self.map_inside(event.x,event.y) else None

    def drag_map_pan(self,event):
        if self.map_drag is None or not self.map_bounds:return
        dx=event.x-self.map_drag[0]
        dy=event.y-self.map_drag[1]
        self.map_drag=(event.x,event.y)
        x,y,w,h=self.map_bounds
        self.map_center=(self.map_center[0]-dx*360/(w*self.map_zoom),self.map_center[1]+dy*168/(h*self.map_zoom))
        self.limit_map_center()
        self.render()

    @staticmethod
    def clip_polygon(points,xmin,ymin,xmax,ymax):
        # Clip geographic polygons to the visible map so zooming never draws over other HUD panels.
        for axis,limit,keep_less in [(0,xmin,False),(0,xmax,True),(1,ymin,False),(1,ymax,True)]:
            if not points:break
            output=[]
            previous=points[-1]
            def inside(v):return v[axis]<=limit if keep_less else v[axis]>=limit
            def intersect(a,b):
                delta=b[axis]-a[axis]
                t=(limit-a[axis])/delta if abs(delta)>1e-10 else 0
                return (a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1]))
            for point in points:
                a=inside(previous);b=inside(point)
                if a!=b:output.append(intersect(previous,point))
                if b:output.append(point)
                previous=point
            points=output
        return points

    def draw_world(self, x, y, w, h):
        """Low-glow interactive map: wheel zoom, middle/shift drag pan, country selection."""
        self.map_bounds=(x,y,w,h)
        self.rect(x,y,w,h,'#071219','#293c44')
        z=self.map_zoom
        def xy(lon,lat):
            return (x+w/2+(lon-self.map_center[0])*w*z/360,
                    y+h/2-(lat-self.map_center[1])*h*z/168)
        for lon in range(-180,181,30):
            gx=xy(lon,0)[0]
            if x<=gx<=x+w:self.canvas.create_line(gx,y,gx,y+h,fill='#13242e')
        for lat in range(-60,85,30):
            gy=xy(0,lat)[1]
            if y<=gy<=y+h:self.canvas.create_line(x,gy,x+w,gy,fill='#13242e')
        selected=self.country_selected
        for country in self.world_countries:
            geom=country['geom']
            polys=[geom['coordinates']] if geom['type']=='Polygon' else geom['coordinates']
            code=country['iso2'] or country['iso3']
            active=code==selected
            for poly in polys:
                ring=poly[0]
                projected=[xy(lon,lat) for lon,lat in ring]
                clipped=self.clip_polygon(projected,x,y,x+w,y+h)
                if len(clipped)<3:continue
                pts=[coordinate for point in clipped for coordinate in point]
                obj=self.canvas.create_polygon(*pts,fill='#812f36' if active else '#142d35',outline=ACCENT if active else '#3a5662',width=1.7 if active else .7,tags=('map-country','country_'+str(code)))
                self.canvas.tag_bind(obj,'<Button-1>',lambda event,c=country:self.select_country(c))
                self.canvas.tag_bind(obj,'<Enter>',lambda event,c=country:self.hover_country(c))
                self.canvas.tag_bind(obj,'<Leave>',lambda event:self.canvas.configure(cursor=''))
        # Header and controls drawn after geographic polygons; map does not obscure UI.
        self.text(x+13,y+10,'WORLD MAP  /  SELECT A COUNTRY',10,CYAN,bold=True)
        btn_w=38
        self.btn(x+w-143,y+7,btn_w,'+',lambda:self.adjust_map_zoom(1.3),height=25)
        self.btn(x+w-100,y+7,btn_w,'−',lambda:self.adjust_map_zoom(1/1.3),height=25)
        self.btn(x+w-57,y+7,48,'1:1',self.reset_map_zoom,height=25)
        self.text(x+13,y+h-23,f'휠: 확대/축소  ·  Shift+드래그: 이동  ·  {self.map_zoom:.1f}x  ·  Natural Earth',9,MUTED)

    def hover_country(self,c):
        self.canvas.configure(cursor='hand2')

    def select_country(self,c):
        code=c.get('iso2') or c['iso3']
        self.country_selected=code
        self.country_error=''
        self.country_loading=bool(c.get('iso2') and code not in self.country_info)
        self.country_basic=c
        self.render()
        if not c.get('iso2'):
            self.country_error='상세 국가 코드가 없어 지도 데이터만 표시합니다.'
            self.render()
            return
        if code in self.country_info:
            return
        def job():
            try:
                info=fetch_country_profile(c['iso2']);error=''
            except Exception as e:
                info=None;error=str(e)
            def finish():
                if info:
                    self.country_info[code]=info
                    self.country_time[code]=dt.datetime.now().strftime('%Y-%m-%d %H:%M')
                    try:PROFILE_CACHE.write_text(json.dumps(self.country_info,ensure_ascii=False),encoding='utf-8')
                    except OSError:pass
                if self.country_selected==code:
                    self.country_error=error
                    self.country_loading=False
                self.render()
            self.root.after(0,finish)
        threading.Thread(target=job,daemon=True).start()

    def country_panel(self,x,y,w,h):
        ix,iy,iw,ih=self.panel(x,y,w,h,'COUNTRY PROFILE  /  MAP SELECTION')
        if not self.country_selected:
            self.text(ix,iy+13,'지도에서 국가를 선택하세요.',12,WHITE)
            self.text(ix,iy+43,'국가명 · 수도 · 인구 · 통화 · 언어 · 시간대',9,MUTED,width=iw)
            return
        c=self.country_basic
        code=self.country_selected
        detail=self.country_info.get(code)
        self.text(ix,iy,(detail or {}).get('flag','')+'  '+c['name'],14,WHITE,bold=True,width=iw)
        self.text(ix,iy+29,'CODE  '+code,9,ACCENT)
        if not detail:
            self.text(ix,iy+62,'국가 정보 가져오는 중…' if self.country_loading else '상세 정보 없음',10,MUTED)
            if self.country_error:self.text(ix,iy+85,self.country_error[:60],9,ORANGE,width=iw)
            return
        values=[('CAPITAL',detail['capital']),('POPULATION',f"{detail['population']:,}" if detail['population'] is not None else '정보 없음'),('CURRENCY',detail['currencies']),('LANGUAGE',detail['languages']),('TIMEZONE',detail['timezone'])]
        avail=max(16,(ih-66)/len(values))
        for i,(key,val) in enumerate(values):
            yy=iy+54+i*avail
            self.text(ix,yy,key,9,MUTED)
            self.text(ix+min(111,iw*.36),yy,str(val)[:52],10,WHITE,width=iw-min(115,iw*.36))
        self.text(ix,iy+ih-12,'REST Countries  ·  '+self.country_time.get(code,'saved data'),8,MUTED)

    def compact_telemetry(self,x,y,w,h):
        gap=10
        pw=(w-gap*2)/3
        values=[('CPU',f'{self.cpu:.0f}%',self.cpu_hist,CYAN),('MEMORY',f'{self.ram:.0f}%',self.ram_hist,'#4ca8e9')]
        for i,(label,value,history,color) in enumerate(values):
            xx=x+i*(pw+gap)
            ix,iy,iw,ih=self.panel(xx,y,pw,h,label+'  /  ACTIVITY')
            self.text(ix,iy,value,20,WHITE,bold=True)
            if label=='MEMORY':
                vm=psutil.virtual_memory()
                self.text(ix+91,iy+9,f'{vm.used/2**30:.1f}/{vm.total/2**30:.1f} GB',9,MUTED)
            self.graph(ix,iy+30,iw,max(32,ih-35),history,color)
        xx=x+2*(pw+gap)
        ix,iy,iw,ih=self.panel(xx,y,pw,h,'BATTERY  /  POWER')
        if self.battery is None:
            self.text(ix,iy,'NOT DETECTED',12,MUTED)
        else:
            pct=self.battery.percent
            color=GREEN if pct>35 else ORANGE if pct>15 else '#ff6767'
            self.text(ix,iy,f'{pct:.0f}%',22,color,bold=True)
            self.text(ix+83,iy+10,'CHARGING' if self.battery.power_plugged else 'ON BATTERY',9,MUTED)
            bar_y=iy+42
            self.rect(ix,bar_y,iw,12,'#153a48')
            self.rect(ix+2,bar_y+2,max(0,(iw-4)*pct/100),8,color,color)

    def render(self,_=None):
        c=self.canvas
        w=max(c.winfo_width(),960);h=max(c.winfo_height(),630)
        c.delete('all')
        self.rect(0,0,w,h,BG,BG)
        for gx in range(0,w,74):c.create_line(gx,0,gx,h,fill='#0a1d25')
        for gy in range(0,h,74):c.create_line(0,gy,w,gy,fill='#0a1d25')
        m=16;gap=10
        self.text(w-m,13,self.clock or '--:--:--',12,WHITE,anchor='ne')
        left_w=(w-2*m-gap)*.71
        side_x=m+left_w+gap;side_w=w-side_x-m
        content_top=28;content_bottom=h-24
        telemetry_h=min(144,max(113,h*.17))
        map_h=content_bottom-content_top-telemetry_h-gap
        self.draw_world(m,content_top,left_w,map_h)
        self.compact_telemetry(m,content_top+map_h+gap,left_w,telemetry_h)
        available=content_bottom-content_top
        profile_h=max(192,min(265,available*.39))
        fx_h=max(139,min(180,available*.265))
        news_h=available-profile_h-fx_h-2*gap
        self.country_panel(side_x,content_top,side_w,profile_h)
        fx_y=content_top+profile_h+gap
        x,y,pw,ph=self.panel(side_x,fx_y,side_w,fx_h,'EXCHANGE  /  KRW · JPY · USD')
        if self.fx:
            krw,jpy,day=self.fx
            items=[('USD / KRW',f'{krw:,.2f}'),('USD / JPY',f'{jpy:,.2f}'),('100 JPY / KRW',f'{100*krw/jpy:,.2f}')]
            step=max(26,(ph-33)/4)
            for i,(label,value) in enumerate(items):
                yy=y+i*step
                self.text(x,yy,label,9,MUTED)
                self.text(x+pw,yy,value,11,WHITE,anchor='ne')
                c.create_line(x,yy+21,x+pw,yy+21,fill=LINE)
            self.text(x,y+ph-14,day+'  /  '+self.fx_source,8,ORANGE if self.fx_error else MUTED)
        else:self.text(x,y+8,'FETCHING…' if not self.fx_error else 'UNAVAILABLE',10,ORANGE)
        ny=fx_y+fx_h+gap
        x,y,pw,ph=self.panel(side_x,ny,side_w,news_h,'NEWS  /  QUICK VIEW')
        bw=(pw-12)/3
        for i,code in enumerate(('JP','KR','US')):
            self.btn(x+i*(bw+6),y,bw,code,lambda code=code:self.open_news(code),selected=self.news_open==code,height=25)
        top=y+32
        if not self.news_open:
            self.text(x,top+8,'일본 / 한국 / 미국 선택 시 뉴스 표시',9,MUTED,width=pw)
        elif self.news_loading:
            self.text(x,top+8,'NEWS LOADING…',10,CYAN)
        elif self.news_error:
            self.text(x,top+3,'연결 오류: '+self.news_error[:50],9,ORANGE,width=pw)
            self.btn(x,top+39,75,'RETRY',lambda:self.open_news(self.news_open,refresh=True),height=23)
        else:
            code=self.news_open
            entries=self.news.get(code,[])
            row_h=38
            max_rows=max(1,int((ph-65)/row_h))
            max_page=max(0,(len(entries)-1)//max_rows)
            self.news_page=min(self.news_page,max_page)
            start=self.news_page*max_rows
            for i,entry in enumerate(entries[start:start+max_rows]):
                yy=top+i*row_h
                self.text(x,yy,f'{start+i+1:02d}',9,ACCENT)
                self.text(x+22,yy,keywords(entry['title'],code),9,WHITE,width=pw-28)
                c.create_line(x,yy+row_h-5,x+pw,yy+row_h-5,fill=LINE)
            fy=y+ph-24
            self.btn(x,fy,34,'◀',lambda:self.change_news_page(-1),height=23)
            self.text(x+pw/2,fy+6,f'{self.news_page+1}/{max_page+1}',9,MUTED,anchor='n')
            self.btn(x+pw-34,fy,34,'▶',lambda:self.change_news_page(1),height=23)
        self.text(m,h-18,'F11 전체화면 · ESC 종료 · 오른쪽 클릭 메뉴',8,MUTED)

    def change_news_page(self,delta):
        if not self.news_open:return
        self.news_page=max(0,self.news_page+delta)
        self.render()

    def open_news(self,code,refresh=False):
        if self.news_open != code:self.news_page=0
        self.news_open=code
        self.news_error=''
        if code not in self.news or refresh or (dt.datetime.now()-self.news_requested.get(code,dt.datetime.min)).total_seconds()>600:
            self.news_loading=True
            self.news_requested[code]=dt.datetime.now()
            def job():
                try: items=fetch_news(code);error=''
                except Exception as ex: items=[];error=str(ex)
                def finish():
                    self.news_loading=False
                    self.news_error=error
                    if not error:
                        self.news[code]=items
                        self.news_fetched[code]=dt.datetime.now().strftime('%Y-%m-%d %H:%M')
                    self.render()
                self.root.after(0,finish)
            threading.Thread(target=job,daemon=True).start()
        else:self.news_loading=False
        self.render()

    def close_news(self):
        self.news_open=None
        self.render()

    def restore_fx_cache(self):
        try:
            obj=json.loads(CACHE.read_text(encoding='utf-8'))
            self.fx=(float(obj['krw']),float(obj['jpy']),str(obj['date']))
            self.fx_source='OFFLINE CACHE'
        except (OSError,KeyError,ValueError,TypeError):
            pass

    def load_fx(self):
        if self.fx_loading:return
        self.fx_loading=True
        def job():
            try: result=fetch_fx();error=''
            except Exception as ex: result=None;error=str(ex)
            def finish():
                if result:
                    self.fx=result
                    self.fx_updated=dt.datetime.now()
                    self.fx_source='ONLINE'
                    try:
                        CACHE.write_text(json.dumps({'krw':result[0],'jpy':result[1],'date':result[2]}),encoding='utf-8')
                    except OSError:pass
                elif self.fx:self.fx_source='OFFLINE CACHE / LAST KNOWN'
                self.fx_error=error
                self.fx_loading=False
                self.render()
            self.root.after(0,finish)
        threading.Thread(target=job,daemon=True).start()

    def fx_timer(self):
        self.load_fx()
        self.root.after(1000*60*15,self.fx_timer)

    def tick(self):
        if not self.root.winfo_exists():return
        self.cpu=psutil.cpu_percent(interval=None)
        self.ram=psutil.virtual_memory().percent
        try:
            self.battery=psutil.sensors_battery()
        except (OSError, RuntimeError):
            self.battery=None
        self.cpu_hist.append(self.cpu)
        self.ram_hist.append(self.ram)
        self.clock=dt.datetime.now().strftime('%Y-%m-%d   %H:%M:%S')
        self.render()
        self.root.after(1000,self.tick)

    def toggle_fullscreen(self):
        if self.wallpaper_active:return
        self.root.attributes('-fullscreen',not bool(self.root.attributes('-fullscreen')))

    def show_context(self,e):
        menu=tk.Menu(self.root,tearoff=0,bg=PANEL,fg=WHITE,activebackground=PANEL2,activeforeground=CYAN)
        menu.add_command(label='환율 새로고침',command=self.load_fx)
        menu.add_command(label='전체화면 전환 (F11)',command=self.toggle_fullscreen)
        menu.add_separator()
        menu.add_command(label='종료 (ESC)',command=self.exit)
        menu.tk_popup(e.x_root,e.y_root)

    def exit(self):
        self.root.destroy()


def main():
    p=argparse.ArgumentParser(description='macOS desktop data monitor')
    p.add_argument('--wallpaper',action='store_true',help='Attach at desktop layer on Windows/macOS (experimental)')
    p.add_argument('--window',action='store_true',help='Regular window (recommended for testing)')
    args=p.parse_args()
    root=tk.Tk()
    app=Monitor(root,wallpaper=args.wallpaper and not args.window)
    root.mainloop()

if __name__=='__main__':main()
