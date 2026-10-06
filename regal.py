import os
import re
import json
import time
import socket
import secrets
import logging
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, render_template_string, request, redirect, url_for, session, send_file, abort, flash, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOOKS_DIR = os.path.join(BASE_DIR, "books")
USERS_FILE = os.path.join(BASE_DIR, "users.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
SECRET_FILE = os.path.join(BASE_DIR, "secret_key.txt")
DB_FILE = os.path.join(BASE_DIR, "regal.db")

ALLOWED_EXTENSIONS = {".pdf"}
ACTIVE_WINDOW_SECONDS = 15 * 60
ONLINE_WINDOW_SECONDS = 60
MAX_MESSAGE_LENGTH = 2000

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8080,
    "server_name": "Regal Inklings",
    "max_upload_mb": 500,
}

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
active_users = {}

STYLE_CSS = r"""
:root{--cream:#faf6ee;--card:#fffdf7;--ink:#2f2a24;--gold:#b08d3e;--gold-dark:#8a6d2c;--brown:#6b5433;--muted:#8a8175;--border:#e8dfc9}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--cream);color:var(--ink);font-family:Georgia,"Times New Roman",serif;line-height:1.5;padding:16px}
.wrap{max-width:760px;margin:0 auto}.wrap.wide{max-width:1060px}header.brand{text-align:center;padding:24px 0 8px}header.brand h1{letter-spacing:4px;font-size:1.6rem;color:var(--brown)}header.brand .sub{color:var(--muted);font-style:italic;letter-spacing:1px}
.nav{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin:12px 0 20px}.nav a{color:var(--gold-dark);text-decoration:none;font-weight:bold;padding:6px 12px;border-radius:20px}.nav a:hover,.nav a.active{background:#efe3c2}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 2px 6px rgba(80,60,20,.06)}
.book{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}.book-info{display:flex;gap:14px;align-items:center}.book-icon{font-size:2rem}.book .title{font-size:1.15rem;font-weight:bold}.book .meta{color:var(--muted);font-size:.9rem}
.btn{display:inline-block;background:var(--gold);color:white;border:none;border-radius:10px;padding:12px 22px;font-size:1rem;font-weight:bold;text-decoration:none;cursor:pointer;min-height:48px;font-family:inherit}.btn:hover{background:var(--gold-dark)}.btn.secondary{background:#efe7d3;color:var(--brown)}.btn.secondary:hover{background:#e3d7b8}.btn.danger{background:#b0503c}.btn.danger:hover{background:#8f3e2e}.btn.small{padding:8px 14px;min-height:40px;font-size:.9rem}
input[type=text],input[type=password],input[type=file]{width:100%;padding:12px;border:1px solid var(--border);border-radius:10px;font-size:1rem;background:white;font-family:inherit}.search{margin-bottom:16px}label{display:block;margin:12px 0 4px;font-weight:bold;color:var(--brown)}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}.stat{flex:1;min-width:120px;text-align:center;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 8px}.stat .n{font-size:1.8rem;font-weight:bold;color:var(--gold-dark)}.stat .l{color:var(--muted);text-transform:uppercase;font-size:.75rem;letter-spacing:2px}
table{width:100%;border-collapse:collapse}td,th{padding:10px 8px;border-bottom:1px solid var(--border);text-align:left}th{color:var(--muted);text-transform:uppercase;font-size:.75rem;letter-spacing:1px}
.flash{background:#f3ead0;border:1px solid var(--gold);border-radius:10px;padding:10px 14px;margin-bottom:14px}.login-box{max-width:420px;margin:40px auto;text-align:center}.login-box .emoji{font-size:3rem;margin:10px 0}.error{color:#a33d2a;margin:10px 0}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted)}.inline-form{display:inline}h2{color:var(--brown);letter-spacing:2px;font-size:1.05rem;margin:26px 0 12px;border-bottom:1px solid var(--border);padding-bottom:6px}h3{color:var(--brown);margin-bottom:10px}.gold{color:var(--gold-dark);font-style:italic}
.member{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.member-info{display:flex;gap:12px;align-items:center}.member .title{font-weight:bold;font-size:1.1rem}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}.dot.online{background:#4a9e4a;box-shadow:0 0 0 3px rgba(74,158,74,.15)}.dot.offline{background:#c8c0ae}.badge{display:inline-block;min-width:20px;padding:1px 7px;background:var(--gold);color:white;border-radius:12px;font-size:.75rem;font-weight:bold;text-align:center}
.chat-layout{display:grid;grid-template-columns:240px 1fr;gap:16px;align-items:start}.chat-side{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;box-shadow:0 2px 6px rgba(80,60,20,.06)}.chat-side h3{font-size:.75rem;letter-spacing:2px;color:var(--muted);margin:12px 0 6px}.side-link{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 10px;border-radius:10px;color:var(--ink);text-decoration:none;margin-bottom:4px}.side-link:hover,.side-link.active{background:#efe3c2}
.chat-main{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;display:flex;flex-direction:column;height:70vh;min-height:420px;box-shadow:0 2px 6px rgba(80,60,20,.06)}.chat-head{padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:10px}.chat-head .back{text-decoration:none;color:var(--gold-dark);font-weight:bold;margin-right:8px}.messages{flex:1;overflow-y:auto;padding:6px 2px;display:flex;flex-direction:column;gap:10px}.msg{max-width:75%;align-self:flex-start;animation:fadein .25s ease}.msg.mine{align-self:flex-end;text-align:right}.msg-name{font-size:.8rem;color:var(--muted);margin-bottom:2px}.msg-bubble{background:#f1e9d4;color:var(--ink);border-radius:14px 14px 14px 4px;padding:10px 14px;white-space:pre-wrap;word-break:break-word}.msg.mine .msg-bubble{background:var(--gold);color:#fffdf7;border-radius:14px 14px 4px 14px}.msg-time{font-size:.72rem;color:var(--muted);margin-top:2px}.msg-del{border:none;background:none;color:var(--muted);cursor:pointer;font-size:.9rem;padding:0 4px}.msg-del:hover{color:#b0503c}.empty{text-align:center;color:var(--muted);padding:40px 10px;font-style:italic}.sendbar{display:flex;gap:8px;margin-top:10px}.sendbar textarea{flex:1;resize:none;padding:12px;border:1px solid var(--border);border-radius:12px;font-family:inherit;font-size:1rem;background:white;min-height:48px;max-height:120px}.sendbar .btn{min-height:48px}
.bottomnav{display:none}@keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}.btn,.nav a,.side-link,.card,.badge{transition:background-color .2s ease,color .2s ease,opacity .2s ease,transform .15s ease}.btn:hover,.side-link:hover,.nav a:hover{transform:translateY(-1px)}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}@media(max-width:720px){.chat-layout{grid-template-columns:1fr}.chat-side{display:flex;flex-wrap:wrap;gap:6px;padding:10px}.chat-side h3{width:100%;margin:4px 0 0}.side-link{padding:6px 10px;border:1px solid var(--border);border-radius:20px;margin:0;font-size:.9rem}.chat-main{height:calc(100vh - 320px);min-height:360px}.msg{max-width:85%}.nav{display:none}.bottomnav{display:flex;justify-content:space-around;position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:8px 0;z-index:50}.bottomnav a{text-decoration:none;color:var(--brown);font-size:1.2rem;text-align:center}.bottomnav a span{display:block;font-size:.65rem;letter-spacing:1px}.bottomnav a.active{color:var(--gold-dark);font-weight:bold}body{padding-bottom:64px}}
@media(max-width:560px){.book{flex-direction:column;align-items:stretch;text-align:center}.btn{width:100%;text-align:center}td .btn.small{width:auto}}
"""

APP_JS = r"""
document.addEventListener('click',function(e){const btn=e.target.closest('a.download-btn');if(!btn||btn.dataset.busy)return;btn.dataset.busy='1';const original=btn.textContent;btn.textContent='DOWNLOADING...';setTimeout(()=>btn.textContent='DOWNLOADED',1200);setTimeout(()=>{btn.textContent=original;delete btn.dataset.busy},3000)});
function filterBooks(){const el=document.getElementById('search');if(!el)return;const q=el.value.toLowerCase();document.querySelectorAll('.book-card').forEach(card=>{card.style.display=card.dataset.title.toLowerCase().includes(q)?'':'none'})}
"""

CHAT_JS = r"""
(function(){const box=document.getElementById('messages'),form=document.getElementById('send-form'),body=document.getElementById('body'),sendBtn=document.getElementById('send-btn'),errBox=document.getElementById('chat-error'),navBadge=document.getElementById('nav-badge');let lastCount=0,nearBottom=true;
function roomParams(){if(!box)return null;const dm=box.dataset.dm;return dm?'with='+encodeURIComponent(dm):'room=group'}
function fmtTime(ts){const d=new Date(ts*1000);let h=d.getHours(),m=d.getMinutes();const ap=h>=12?'PM':'AM';h=h%12||12;return h+':'+(m<10?'0':'')+m+' '+ap}
function appendMessage(m){const wrap=document.createElement('div');wrap.className='msg'+(m.mine?' mine':'');const name=document.createElement('div');name.className='msg-name';name.textContent=m.mine?'You':m.sender;const bubble=document.createElement('div');bubble.className='msg-bubble';bubble.textContent=m.body;const meta=document.createElement('div');meta.className='msg-time';meta.textContent=fmtTime(m.ts);wrap.append(name,bubble,meta);if(m.mine){const del=document.createElement('button');del.className='msg-del';del.textContent='×';del.title='Delete message';del.onclick=()=>deleteMessage(m.id,wrap);wrap.appendChild(del)}box.appendChild(wrap)}
function renderMessages(msgs){if(msgs.length!==lastCount){box.innerHTML='';if(!msgs.length){const e=document.createElement('div');e.className='empty';e.innerHTML=box.dataset.empty||'';box.appendChild(e)}else msgs.forEach(appendMessage);lastCount=msgs.length;if(nearBottom)box.scrollTop=box.scrollHeight}}
async function poll(){if(!box){pollUnread();return}try{const r=await fetch('/api/messages?'+roomParams(),{credentials:'same-origin'});if(!r.ok)return;const data=await r.json();renderMessages(data.messages);if(data.other_online!==undefined){const st=document.getElementById('dm-status');if(st)st.textContent=data.other_online?'Online':'Offline'}pollUnread()}catch(e){}}
async function pollUnread(){try{const r=await fetch('/api/unread',{credentials:'same-origin'});if(!r.ok)return;const u=await r.json();const g=document.getElementById('badge-group');if(g){g.style.display=u.group>0?'':'none';g.textContent=u.group}document.querySelectorAll('.badge[data-dm]').forEach(b=>{const n=u.dms[b.dataset.dm]||0;b.style.display=n>0?'':'none';b.textContent=n});if(navBadge){navBadge.style.display=u.total>0?'':'none';navBadge.textContent=u.total}document.title=u.total?'('+u.total+') Regal Inklings':'Regal Inklings'}catch(e){}}
async function deleteMessage(id,el){const csrf=document.getElementById('csrf');try{const r=await fetch('/api/messages/'+id+'/delete',{method:'POST',headers:{'X-CSRFToken':csrf?csrf.value:''},credentials:'same-origin'});if(r.ok){el.style.opacity='0';setTimeout(()=>{el.remove();lastCount--},200)}else showErr('Unable to delete message.')}catch(e){showErr('Unable to delete message.')}}
function showErr(t){if(!errBox)return;errBox.textContent=t;errBox.style.display='';setTimeout(()=>errBox.style.display='none',4000)}
if(form)form.addEventListener('submit',async e=>{e.preventDefault();const text=body.value.trim();if(!text)return;sendBtn.disabled=true;const csrf=document.getElementById('csrf');try{const dm=box.dataset.dm;const payload=dm?{with:dm,body:text}:{room:'group',body:text};const r=await fetch('/api/messages',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf?csrf.value:''},body:JSON.stringify(payload),credentials:'same-origin'});if(r.ok){body.value='';lastCount=-1;await poll()}else{const d=await r.json().catch(()=>({}));showErr(d.error||(r.status===403?'You do not have permission to perform this action.':'Unable to send message.'))}}catch(e2){showErr('Unable to send message.')}sendBtn.disabled=false});
if(body)body.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.dispatchEvent(new Event('submit'))}});
if(box){box.dataset.empty=box.dataset.dm?'No messages yet.<br>Start the conversation.':"Welcome to Regal Inklings.<br>This is the group's reading room.<br>Start the conversation.";box.addEventListener('scroll',()=>{nearBottom=box.scrollHeight-box.scrollTop-box.clientHeight<60});poll();setInterval(poll,2000)}else{pollUnread();setInterval(pollUnread,3000)}})();
"""

NAV = r"""
<nav class="nav">
{% if session.get('role') == 'admin' %}<a href="{{ url_for('admin') }}" class="{{ 'active' if page == 'admin' }}">DASHBOARD</a>{% endif %}
<a href="{{ url_for('library') }}" class="{{ 'active' if page == 'library' }}">LIBRARY</a>
<a href="{{ url_for('chat') }}" class="{{ 'active' if page == 'chat' }}">CHAT <span id="nav-badge" class="badge" style="display:none"></span></a>
<a href="{{ url_for('members') }}" class="{{ 'active' if page == 'members' }}">MEMBERS</a><a href="{{ url_for('logout') }}">LOGOUT</a></nav>
<nav class="bottomnav">{% if session.get('role') == 'admin' %}<a href="{{ url_for('admin') }}" class="{{ 'active' if page == 'admin' }}">&#128202;<span>Admin</span></a>{% endif %}
<a href="{{ url_for('library') }}" class="{{ 'active' if page == 'library' }}">&#128218;<span>Library</span></a><a href="{{ url_for('chat') }}" class="{{ 'active' if page == 'chat' }}">&#128172;<span>Chat</span></a><a href="{{ url_for('members') }}" class="{{ 'active' if page == 'members' }}">&#128101;<span>Members</span></a><a href="{{ url_for('logout') }}">&#128682;<span>Logout</span></a></nav>
"""

LOGIN_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Regal Inklings — Sign In</title><link rel="stylesheet" href="{{ url_for('style_css') }}"></head><body><div class="wrap"><header class="brand"><h1>REGAL INKLINGS</h1><div class="sub">Member Library</div></header><div class="card login-box"><div class="emoji">📚</div>{% if error %}<p class="error">{{ error }}</p>{% endif %}<form method="post" action="{{ url_for('login') }}"><label>Username</label><input type="text" name="username" autocomplete="username" required autofocus><label>Password</label><input type="password" name="password" autocomplete="current-password" required><p style="margin-top:16px"><button class="btn" type="submit">SIGN IN</button></p></form><p class="muted" style="margin-top:14px">Regal Inklings Library</p></div></div></body></html>"""

LIBRARY_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Regal Inklings — Book Library</title><link rel="stylesheet" href="{{ url_for('style_css') }}"><script src="{{ url_for('app_js') }}" defer></script></head><body>{% set page='library' %}<div class="wrap"><header class="brand"><h1>REGAL INKLINGS</h1><div class="sub">Your private reading room.</div></header><p style="text-align:center">Welcome, <strong>{{ username }}</strong></p>{{ nav|safe }}<h2>AVAILABLE BOOKS</h2><input id="search" class="search" type="text" placeholder="Search the library..." oninput="filterBooks()">{% if books %}{% for book in books %}<div class="card book book-card" data-title="{{ book.title }}"><div class="book-info"><div class="book-icon">📖</div><div><div class="title">{{ book.title }}</div><div class="meta">PDF &bull; {{ book.size_human }}{% if book.mtime and (now-book.mtime)<604800 %} &bull; <span class="gold">Added recently</span>{% endif %}</div></div></div><a class="btn small download-btn" href="{{ url_for('download',filename=book.filename) }}" download>DOWNLOAD PDF</a></div>{% endfor %}{% else %}<div class="card" style="text-align:center"><p><strong>NO BOOKS AVAILABLE</strong></p><p>The library is currently empty.</p><p class="muted">Check back once books have been added.</p></div>{% endif %}</div></body></html>"""

MEMBERS_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Regal Inklings — Members</title><link rel="stylesheet" href="{{ url_for('style_css') }}"><script src="{{ url_for('chat_js') }}" defer></script></head><body>{% set page='members' %}<div class="wrap"><header class="brand"><h1>REGAL INKLINGS</h1><div class="sub">The reading circle.</div></header><p style="text-align:center">Signed in as <strong>{{ username }}</strong></p>{{ nav|safe }}<h2>MEMBERS</h2>{% for m in members %}<div class="card member"><div class="member-info"><span class="dot {{ 'online' if m.online else 'offline' }}"></span><div><div class="title">{{ m.username }}{% if m.is_me %} <span class="muted">(you)</span>{% endif %}</div><div class="meta">{{ 'Administrator' if m.role=='admin' else 'Member' }} &bull; {{ 'Online' if m.online else 'Offline' }}</div></div></div>{% if not m.is_me %}<a class="btn small secondary" href="{{ url_for('chat',with=m.username) }}">MESSAGE</a>{% endif %}</div>{% endfor %}</div></body></html>"""

CHAT_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Regal Inklings — Chat</title><link rel="stylesheet" href="{{ url_for('style_css') }}"><script src="{{ url_for('app_js') }}"></script><script src="{{ url_for('chat_js') }}" defer></script></head><body>{% set page='chat' %}<div class="wrap wide"><header class="brand"><h1>REGAL INKLINGS</h1><div class="sub">The reading room.</div></header>{{ nav|safe }}<div class="chat-layout"><aside class="chat-side"><h3>GROUPS</h3><a href="{{ url_for('chat') }}" class="side-link {{ 'active' if not dm }}">💬 Regal Inklings <span class="badge" id="badge-group" style="display:none"></span></a><h3>DIRECT MESSAGES</h3><div>{% for other in others %}<a href="{{ url_for('chat',with=other) }}" class="side-link {{ 'active' if dm==other }}">{{ other }} <span class="badge" data-dm="{{ other }}" style="display:none"></span></a>{% endfor %}</div></aside><main class="chat-main"><div class="chat-head">{% if dm %}<a href="{{ url_for('chat') }}" class="back">←</a><strong>{{ dm }}</strong> <span class="dot {{ 'online' if dm_online else 'offline' }}"></span> <span class="muted" id="dm-status">{{ 'Online' if dm_online else 'Offline' }}</span>{% else %}<strong>REGAL INKLINGS</strong> <span class="muted">Group Chat</span>{% endif %}</div><div id="messages" class="messages" data-dm="{{ dm or '' }}"></div><form id="send-form" class="sendbar"><input type="hidden" id="csrf" value="{{ csrf_token() }}"><textarea id="body" rows="1" maxlength="2000" placeholder="Type a message..."></textarea><button class="btn small" type="submit" id="send-btn">SEND</button></form><p id="chat-error" class="error" style="display:none"></p></main></div></div></body></html>"""

ERROR_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Regal Inklings — {{ code }}</title><link rel="stylesheet" href="{{ url_for('style_css') }}"></head><body><div class="wrap"><header class="brand"><h1>REGAL INKLINGS</h1></header><div class="card" style="text-align:center"><h2>{{ code }}</h2><p>{{ message }}</p><p style="margin-top:16px"><a class="btn" href="{{ url_for('library') }}">RETURN TO LIBRARY</a></p></div></div></body></html>"""

UPLOAD_OK_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Regal Inklings — Upload</title><link rel="stylesheet" href="{{ url_for('style_css') }}"></head><body><div class="wrap"><header class="brand"><h1>REGAL INKLINGS</h1></header><div class="card" style="text-align:center"><h2>Upload successful!</h2><p><strong>{{ filename }}</strong></p><p style="margin-top:16px"><a class="btn" href="{{ url_for('admin') }}">BACK TO ADMIN PANEL</a></p></div></div></body></html>"""

ADMIN_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Regal Inklings — Admin</title><link rel="stylesheet" href="{{ url_for('style_css') }}"><script src="{{ url_for('app_js') }}" defer></script></head><body>{% set page='admin' %}<div class="wrap wide"><header class="brand"><h1>REGAL INKLINGS</h1><div class="sub">The librarian's desk — {{ display_name }}</div></header>{{ nav|safe }}{% with messages=get_flashed_messages() %}{% for m in messages %}<div class="flash">{{ m }}</div>{% endfor %}{% endwith %}<h2>OVERVIEW</h2><div class="stats"><div class="stat"><div class="n">{{ books|length }}</div><div class="l">Books</div></div><div class="stat"><div class="n">{{ member_count }}</div><div class="l">Members</div></div><div class="stat"><div class="n">{{ online_count }}</div><div class="l">Online Now</div></div></div><h2>BOOK LIBRARY</h2><div class="card">{% if books %}<table>{% for book in books %}<tr><td>{{ book.filename }}</td><td class="muted">{{ book.size_human }}</td><td style="text-align:right"><form class="inline-form" method="post" action="{{ url_for('delete_book') }}" onsubmit="return confirm('Delete this book?')"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><input type="hidden" name="filename" value="{{ book.filename }}"><button class="btn danger small">DELETE</button></form></td></tr>{% endfor %}</table>{% else %}<p>No books have been uploaded yet.</p>{% endif %}</div><div class="card"><h3>Upload PDF</h3><form method="post" action="{{ url_for('upload') }}" enctype="multipart/form-data" id="upload-form"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><input type="file" name="book" accept=".pdf" required><p style="margin-top:12px"><button class="btn" type="submit" id="upload-btn">UPLOAD PDF</button></p></form></div><h2>MEMBERS</h2><div class="card"><h3>Create Student Account</h3><form method="post" action="{{ url_for('create_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><label>Username</label><input type="text" name="username" required><label>Password</label><input type="text" name="password" required><p style="margin-top:12px"><button class="btn">CREATE ACCOUNT</button></p></form></div><div class="card"><table>{% for u in students %}<tr><td>{{ u.username }}</td><td><form class="inline-form row" method="post" action="{{ url_for('reset_password') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><input type="hidden" name="username" value="{{ u.username }}"><input type="text" name="password" placeholder="New password" required style="max-width:160px"><button class="btn small secondary">RESET PASSWORD</button></form></td><td style="text-align:right"><form class="inline-form" method="post" action="{{ url_for('delete_user') }}" onsubmit="return confirm('Delete this user?')"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><input type="hidden" name="username" value="{{ u.username }}"><button class="btn danger small">DELETE</button></form></td></tr>{% endfor %}</table></div><h2>CHAT</h2><div class="card"><p><a class="btn secondary small" href="{{ url_for('chat') }}">OPEN GROUP CHAT</a></p><h3 style="margin-top:16px">Recent Group Messages</h3>{% if recent_messages %}<table>{% for m in recent_messages %}<tr><td><strong>{{ m.sender }}</strong><br><span class="muted">{{ m.body[:80] }}</span></td><td style="text-align:right"><form class="inline-form" method="post" action="{{ url_for('admin_delete_message') }}" onsubmit="return confirm('Delete this message?')"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><input type="hidden" name="id" value="{{ m.id }}"><button class="btn danger small">DELETE</button></form></td></tr>{% endfor %}</table>{% else %}<p class="muted">No group messages yet.</p>{% endif %}</div><div class="card"><h3>Change Admin Password</h3><form method="post" action="{{ url_for('change_password') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><label>Current Password</label><input type="password" name="current_password" required><label>New Password</label><input type="password" name="new_password" required><p style="margin-top:12px"><button class="btn">CHANGE PASSWORD</button></p></form></div></div><script>document.getElementById('upload-form').addEventListener('submit',function(){document.getElementById('upload-btn').disabled=true;document.getElementById('upload-btn').textContent='UPLOADING...'});</script></body></html>"""

def timestamp():
    return datetime.now().strftime("%H:%M:%S")

def log_event(msg):
    print(f"[{timestamp()}] {msg}")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(DEFAULT_CONFIG, f, indent=4)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f: cfg = json.load(f)
    for k,v in DEFAULT_CONFIG.items(): cfg.setdefault(k,v)
    return cfg

def load_users():
    with open(USERS_FILE, "r", encoding="utf-8") as f: return json.load(f)["users"]

def save_users(users):
    tmp=USERS_FILE+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump({"users":users},f,indent=4)
    os.replace(tmp,USERS_FILE)

def find_user(username):
    return next((u for u in load_users() if u["username"]==username),None)

def create_default_users():
    password=os.environ.get("REGAL_DEFAULT_ADMIN_PASSWORD")
    if not password: raise RuntimeError("REGAL_DEFAULT_ADMIN_PASSWORD is required when creating default users.")
    users=[{"username":"k4ge","password_hash":generate_password_hash(password),"role":"admin","display_name":"Manan Sulya"}]
    save_users(users); return users

def get_secret_key():
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE,"r",encoding="utf-8") as f:return f.read().strip()
    key=secrets.token_hex(32)
    with open(SECRET_FILE,"w",encoding="utf-8") as f:f.write(key)
    return key

def get_lan_ip():
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(("8.8.8.8",80)); ip=s.getsockname()[0]; s.close(); return ip
    except OSError:
        try:return socket.gethostbyname(socket.gethostname())
        except OSError:return "127.0.0.1"

def human_size(num):
    for unit in ["B","KB","MB","GB","TB"]:
        if num<1024 or unit=="TB": return f"{num} {unit}" if unit=="B" else f"{num:.1f} {unit}"
        num/=1024

def list_books():
    out=[]
    if os.path.isdir(BOOKS_DIR):
        for name in sorted(os.listdir(BOOKS_DIR),key=str.lower):
            p=os.path.join(BOOKS_DIR,name)
            if os.path.isfile(p) and os.path.splitext(name)[1].lower() in ALLOWED_EXTENSIONS:
                size=os.path.getsize(p)
                out.append({"filename":name,"title":os.path.splitext(name)[0],"ext":"PDF","size":size,"size_human":human_size(size),"mtime":os.path.getmtime(p)})
    return out

def safe_book_path(filename):
    if not filename or filename in (".",".."): return None
    candidate=os.path.realpath(os.path.join(BOOKS_DIR,filename)); root=os.path.realpath(BOOKS_DIR)
    if candidate!=root and candidate.startswith(root+os.sep) and os.path.isfile(candidate) and os.path.splitext(candidate)[1].lower() in ALLOWED_EXTENSIONS:return candidate
    return None

def touch_active(username): active_users[username]=time.time()
def active_now(): return sorted(u for u,t in active_users.items() if t>=time.time()-ACTIVE_WINDOW_SECONDS)
def is_online(username):
    t=active_users.get(username); return t is not None and time.time()-t<=ONLINE_WINDOW_SECONDS

def login_required(role=None):
    def deco(view):
        @wraps(view)
        def wrapped(*args,**kwargs):
            user=session.get("username")
            if not user:return redirect(url_for("login"))
            if role and session.get("role")!=role:abort(403)
            touch_active(user); return view(*args,**kwargs)
        return wrapped
    return deco

def csrf_token():
    token=session.get("csrf_token")
    if not token: token=secrets.token_hex(16); session["csrf_token"]=token
    return token

def csrf_ok(): return bool(request.form.get("csrf_token","") and request.form.get("csrf_token")==session.get("csrf_token"))
def csrf_header_ok(): return bool(request.headers.get("X-CSRFToken","") and request.headers.get("X-CSRFToken")==session.get("csrf_token"))

def db():
    conn=sqlite3.connect(DB_FILE); conn.row_factory=sqlite3.Row; return conn

def init_db():
    conn=db()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT,sender TEXT NOT NULL,body TEXT NOT NULL,ts REAL NOT NULL,room TEXT NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room,id)")
        conn.execute("CREATE TABLE IF NOT EXISTS reads (username TEXT NOT NULL,room TEXT NOT NULL,last_id INTEGER NOT NULL,PRIMARY KEY(username,room))")
        conn.commit()
    finally: conn.close()

def dm_room(a,b): return "dm:"+":".join(sorted([a,b]))

def room_for_request(args,username):
    other=str(args.get("with","")).strip()
    if other:
        if other==username or not find_user(other):return None
        return dm_room(username,other)
    return "group" if args.get("room","group")=="group" else None

def unread_counts(username):
    conn=db()
    try:
        rows=conn.execute("SELECT room,MAX(id) max_id FROM messages WHERE sender!=? GROUP BY room",(username,)).fetchall()
        reads={r["room"]:r["last_id"] for r in conn.execute("SELECT room,last_id FROM reads WHERE username=?",(username,))}
        group=0; dms={}
        for row in rows:
            room=row["room"]; last=reads.get(room,0)
            n=conn.execute("SELECT COUNT(*) c FROM messages WHERE room=? AND id>? AND sender!=?",(room,last,username)).fetchone()["c"]
            if n:
                if room=="group":group=n
                else:
                    parts=room.split(":")[1:]; other=parts[0] if parts[1]==username else parts[1]; dms[other]=n
        return {"group":group,"dms":dms,"total":group+sum(dms.values())}
    finally:conn.close()

def mark_read(username,room):
    conn=db()
    try:
        last=conn.execute("SELECT MAX(id) m FROM messages WHERE room=?",(room,)).fetchone()["m"] or 0
        conn.execute("INSERT INTO reads(username,room,last_id) VALUES(?,?,?) ON CONFLICT(username,room) DO UPDATE SET last_id=excluded.last_id",(username,room,last));conn.commit()
    finally:conn.close()

def render(t,**ctx):
    ctx["nav"]=NAV
    return render_template_string(t,**ctx)

def create_app():
    config=load_config(); os.makedirs(BOOKS_DIR,exist_ok=True); first_run=not os.path.exists(USERS_FILE)
    if first_run:create_default_users()
    init_db()
    app=Flask(__name__); app.secret_key=get_secret_key(); app.config["MAX_CONTENT_LENGTH"]=int(config["max_upload_mb"])*1024*1024
    app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax")
    app.jinja_env.globals["csrf_token"]=csrf_token

    @app.route("/static/style.css")
    def style_css():return Response(STYLE_CSS,mimetype="text/css")
    @app.route("/static/app.js")
    def app_js():return Response(APP_JS,mimetype="application/javascript")
    @app.route("/static/chat.js")
    def chat_js():return Response(CHAT_JS,mimetype="application/javascript")

    @app.route("/login",methods=["GET","POST"])
    def login():
        if session.get("username"):return redirect(url_for("library"))
        error=None
        if request.method=="POST":
            username=request.form.get("username","").strip(); password=request.form.get("password",""); user=find_user(username)
            if user and check_password_hash(user["password_hash"],password):
                session.clear(); session["username"]=user["username"]; session["role"]=user["role"]; session.permanent=False; touch_active(user["username"])
                log_event(f"{user['username']} logged in"); return redirect(url_for("admin" if user["role"]=="admin" else "library"))
            error="Invalid username or password."; log_event(f"Failed login attempt for '{username}'")
        return render_template_string(LOGIN_HTML,error=error)

    @app.route("/logout",methods=["GET","POST"])
    def logout():
        username=session.get("username"); session.clear()
        if username:log_event(f"{username} logged out")
        return redirect(url_for("login"))

    @app.route("/")
    @login_required()
    def library():return render(LIBRARY_HTML,username=session["username"],role=session.get("role"),now=time.time(),books=list_books())

    @app.route("/download/<path:filename>")
    @login_required()
    def download(filename):
        path=safe_book_path(filename)
        if path is None:abort(404)
        log_event(f"{session['username']} downloaded {os.path.basename(path)}")
        return send_file(path,as_attachment=True,download_name=os.path.basename(path))

    @app.route("/members")
    @login_required()
    def members():
        me=session["username"]; users=load_users()
        ml=[{"username":u["username"],"role":u["role"],"online":is_online(u["username"]),"is_me":u["username"]==me} for u in users]
        ml.sort(key=lambda m:(m["role"]!="admin",m["username"].lower()))
        return render(MEMBERS_HTML,members=ml,username=me,role=session.get("role"))

    @app.route("/chat")
    @login_required()
    def chat():
        users=load_users(); others=[u["username"] for u in users if u["username"]!=session["username"]]; dm=request.args.get("with","").strip()
        if dm and (dm==session["username"] or not find_user(dm)):return redirect(url_for("chat"))
        return render(CHAT_HTML,username=session["username"],role=session.get("role"),dm=dm or None,dm_online=is_online(dm) if dm else False,others=others)

    @app.route("/messages")
    @login_required()
    def messages_page():return redirect(url_for("chat"))

    @app.route("/api/messages",methods=["GET"])
    @login_required()
    def api_messages():
        room=room_for_request(request.args,session["username"])
        if room is None:abort(403)
        conn=db()
        try:
            rows=conn.execute("SELECT id,sender,body,ts FROM messages WHERE room=? ORDER BY id DESC LIMIT 200",(room,)).fetchall()
            msgs=[{"id":r["id"],"sender":r["sender"],"body":r["body"],"ts":r["ts"],"mine":r["sender"]==session["username"]} for r in reversed(rows)]
        finally:conn.close()
        mark_read(session["username"],room); resp={"messages":msgs,"me":session["username"]}
        if request.args.get("with"):resp["other_online"]=is_online(request.args.get("with"))
        return jsonify(resp)

    @app.route("/api/messages",methods=["POST"])
    @login_required()
    def api_send():
        if not csrf_header_ok():abort(403)
        data=request.get_json(silent=True) or {}; room=room_for_request(data,session["username"])
        if room is None:abort(403)
        body=(data.get("body") or "").strip()
        if not body:return jsonify({"error":"Message cannot be empty."}),400
        if len(body)>MAX_MESSAGE_LENGTH:return jsonify({"error":"Message is too long (max 2000 characters)."}),400
        conn=db()
        try:
            cur=conn.execute("INSERT INTO messages(sender,body,ts,room) VALUES(?,?,?,?)",(session["username"],body,time.time(),room));mid=cur.lastrowid;conn.commit()
        finally:conn.close()
        return jsonify({"id":mid,"sender":session["username"],"body":body,"ts":time.time(),"mine":True})

    @app.route("/api/messages/<int:mid>/delete",methods=["POST"])
    @login_required()
    def api_delete_message(mid):
        if not csrf_header_ok():abort(403)
        conn=db()
        try:
            row=conn.execute("SELECT sender FROM messages WHERE id=?",(mid,)).fetchone()
            if row is None:abort(404)
            if row["sender"]!=session["username"] and session.get("role")!="admin":abort(403)
            conn.execute("DELETE FROM messages WHERE id=?",(mid,));conn.commit()
        finally:conn.close()
        return jsonify({"ok":True})

    @app.route("/api/unread")
    @login_required()
    def api_unread():return jsonify(unread_counts(session["username"]))

    @app.route("/admin/delete-message",methods=["POST"])
    @login_required(role="admin")
    def admin_delete_message():
        if not csrf_ok():abort(403)
        try:mid=int(request.form.get("id","0"))
        except ValueError:abort(400)
        conn=db()
        try:conn.execute("DELETE FROM messages WHERE id=?",(mid,));conn.commit()
        finally:conn.close()
        flash("Message deleted.");return redirect(url_for("admin"))

    @app.route("/admin")
    @login_required(role="admin")
    def admin():
        users=load_users();students=[u for u in users if u["role"]=="student"];books=list_books()
        conn=db()
        try:recent=[dict(r) for r in conn.execute("SELECT id,sender,body,ts FROM messages WHERE room='group' ORDER BY id DESC LIMIT 20").fetchall()]
        finally:conn.close()
        me=find_user(session["username"]) or {}
        return render(ADMIN_HTML,users=users,students=students,books=books,total_size=human_size(sum(b["size"] for b in books)),active=active_now(),username=session["username"],online_count=sum(is_online(u["username"]) for u in users),member_count=len(users),recent_messages=recent,display_name=me.get("display_name",session["username"]))

    @app.route("/admin/upload",methods=["POST"])
    @login_required(role="admin")
    def upload():
        if not csrf_ok():abort(403)
        file=request.files.get("book")
        if not file or not file.filename:flash("No file selected.");return redirect(url_for("admin"))
        if os.path.splitext(file.filename)[1].lower() not in ALLOWED_EXTENSIONS:flash("Only PDF (.pdf) files are allowed.");return redirect(url_for("admin"))
        filename=os.path.basename(file.filename.replace("\\","/").split("/")[-1]).strip()
        if not filename or filename in (".",".."):flash("Invalid filename.");return redirect(url_for("admin"))
        filename=re.sub(r'[<>:"|?*\x00-\x1f]',"_",filename);dest=os.path.join(BOOKS_DIR,filename)
        if os.path.exists(dest):
            base,ext=os.path.splitext(filename);n=2
            while os.path.exists(dest):dest=os.path.join(BOOKS_DIR,f"{base} ({n}){ext}");n+=1
            filename=os.path.basename(dest)
        file.save(dest);log_event(f"admin uploaded {filename}");return render_template_string(UPLOAD_OK_HTML,filename=filename)

    @app.route("/admin/delete-book",methods=["POST"])
    @login_required(role="admin")
    def delete_book():
        if not csrf_ok():abort(403)
        path=safe_book_path(request.form.get("filename",""))
        if path is None:abort(403)
        os.remove(path);flash(f"Deleted {os.path.basename(path)}.");return redirect(url_for("admin"))

    @app.route("/admin/create-user",methods=["POST"])
    @login_required(role="admin")
    def create_user():
        if not csrf_ok():abort(403)
        username=request.form.get("username","").strip();password=request.form.get("password","")
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,32}",username or ""):flash("Username must be 3-32 letters, numbers, _ or -.");return redirect(url_for("admin"))
        if len(password)<4:flash("Password must be at least 4 characters.");return redirect(url_for("admin"))
        users=load_users()
        if any(u["username"]==username for u in users):flash("That username already exists.");return redirect(url_for("admin"))
        users.append({"username":username,"password_hash":generate_password_hash(password),"role":"student"});save_users(users);flash(f"Created user {username}.");return redirect(url_for("admin"))

    @app.route("/admin/reset-password",methods=["POST"])
    @login_required(role="admin")
    def reset_password():
        if not csrf_ok():abort(403)
        username=request.form.get("username","");new=request.form.get("password","")
        if len(new)<4:flash("Password must be at least 4 characters.");return redirect(url_for("admin"))
        users=load_users()
        for u in users:
            if u["username"]==username and u["role"]=="student":
                u["password_hash"]=generate_password_hash(new);save_users(users);flash(f"Password reset for {username}.");return redirect(url_for("admin"))
        flash("Student not found.");return redirect(url_for("admin"))

    @app.route("/admin/delete-user",methods=["POST"])
    @login_required(role="admin")
    def delete_user():
        if not csrf_ok():abort(403)
        username=request.form.get("username","");users=load_users();remaining=[u for u in users if not(u["username"]==username and u["role"]=="student")]
        if len(remaining)==len(users):flash("Student not found.")
        else:save_users(remaining);active_users.pop(username,None);flash(f"Deleted user {username}.")
        return redirect(url_for("admin"))

    @app.route("/admin/change-password",methods=["POST"])
    @login_required(role="admin")
    def change_password():
        if not csrf_ok():abort(403)
        current=request.form.get("current_password","");new=request.form.get("new_password","");user=find_user(session["username"])
        if not user or not check_password_hash(user["password_hash"],current):flash("Current password is incorrect.");return redirect(url_for("admin"))
        if len(new)<4:flash("New password must be at least 4 characters.");return redirect(url_for("admin"))
        users=load_users()
        for u in users:
            if u["username"]==session["username"]:u["password_hash"]=generate_password_hash(new)
        save_users(users);flash("Password updated.");return redirect(url_for("admin"))

    @app.errorhandler(403)
    def forbidden(e):return render_template_string(ERROR_HTML,code=403,message="Access denied."),403
    @app.errorhandler(404)
    def not_found(e):return render_template_string(ERROR_HTML,code=404,message="That page could not be found."),404
    @app.errorhandler(413)
    def too_large(e):return render_template_string(ERROR_HTML,code=413,message="That file is too large."),413
    @app.errorhandler(500)
    def server_error(e):return render_template_string(ERROR_HTML,code=500,message="Something went wrong."),500
    return app,config

app,config=create_app()

if __name__=="__main__":
    lan=get_lan_ip();port=int(config["port"])
    print("="*44);print("       REGAL INKLINGS SERVER");print("="*44);print()
    print(f"Local:   http://127.0.0.1:{port}");print(f"Network: http://{lan}:{port}");print()
    print(f"Students: http://{lan}:{port}");print(f"Admin:    http://{lan}:{port}/admin");print()
    print("Press CTRL+C to stop the server.");print("="*44);log_event("Server started")
    app.run(host=config["host"],port=port,threaded=True,debug=False)
