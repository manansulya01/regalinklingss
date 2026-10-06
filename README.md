# Regal Inklings

A lightweight private book-club server for **Regal Inklings**.

The project is intentionally packaged as a **single Python file** (`regal.py`) while keeping the functionality of the original multi-file Flask app.

## Features

- 📚 PDF book library
- 👤 Student accounts
- 🔐 Admin account and administration panel
- 💬 Group chat
- 💬 Direct messages between members
- 🔔 Unread message indicators
- 🟢 Online/member status
- 📤 Admin PDF uploads
- 🗑️ Admin book and message management
- 🔑 Password changes and resets
- 💾 SQLite message storage
- 🧩 No separate HTML/CSS/JS files required
- 🌐 Designed to work over a local network (LAN)

## Project structure

```text
regalinklingss/
└── regal.py
```

Runtime data is stored locally in folders/files such as `books/`, `users.json`, `config.json`, `secret_key.txt`, and `regal.db`. These contain local/private data and should generally not be committed to GitHub.

## Requirements

- Python 3.10+ recommended
- Flask

Install Flask:

```powershell
py -m pip install flask
```

## Run the server

```powershell
git clone https://github.com/manansulya01/regalinklingss.git
cd regalinklingss
py -m py_compile regal.py
py regal.py
```

By default the server uses port **8080**. Open `http://127.0.0.1:8080` locally. For other devices on the same Wi-Fi/LAN, use the **Network** address printed by the server.

## Default admin account

- **Username:** `k4ge`
- **Password:** `Regal123!`
- **Display name:** `Manan Sulya`

**Change the admin password immediately after first login.**

You can set a custom first-run admin password:

```powershell
$env:REGAL_DEFAULT_ADMIN_PASSWORD="your-strong-password"
py regal.py
```

## Configuration

If `config.json` does not exist, the application creates it automatically.

```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "server_name": "Regal Inklings",
  "max_upload_mb": 500
}
```

If Windows refuses port 8080, change the port to another free port such as 8081 and restart the server.

## LAN access

The server listens on `0.0.0.0`, allowing devices on the same network to connect.

If another device cannot connect:

1. Make sure both devices are on the same network.
2. Start `regal.py`.
3. Use the **Network** URL printed in the terminal.
4. Check Windows Firewall if the connection is blocked.
5. Make sure the configured port is available.

Check port 8080 with:

```powershell
netstat -ano | findstr :8080
```

## Security notes

This application is intended primarily for a trusted/private LAN or small club environment.

For public deployment, add HTTPS/TLS, a production WSGI server, strong unique passwords, secure secret management, rate limiting, backups, and restrictive firewall/network rules.

Do **not** commit `users.json`, `secret_key.txt`, `regal.db`, uploaded books, passwords, or other private data.

## Troubleshooting

### Flask is missing

```powershell
py -m pip install flask
```

### Port 8080 cannot be opened

Change the port in `config.json` to another available port, such as `8081`.

### Other devices cannot connect

Check Windows Firewall and confirm that the server is listening on `0.0.0.0` and that the correct Network URL is being used.

### Login/session problems after moving the project

Do not delete `users.json` or `regal.db` unless you intentionally want to reset local data. The application stores its session secret in `secret_key.txt`.

## Development

Compile-check:

```powershell
py -m py_compile regal.py
```

Run:

```powershell
py regal.py
```

The project is intentionally kept as a single Python source file so it can be copied, moved, or deployed easily without a separate templates/static directory.

## License

No license has been specified yet. Add a license file before distributing the project publicly.