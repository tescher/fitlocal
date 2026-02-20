# FitLocal

A personal workout trainer that runs on your home network. The app and data live on your desktop computer; access it from any device (tablet, laptop, phone) in your home via a web browser.

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Your Anthropic API Key

Create a `.env` file in the `fitlocal/` directory (copy from the example):

```bash
cp .env.example .env
```

Edit `.env` and add your key:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 3. Run the App

```bash
python app.py
```

The database is created automatically on first run. The server starts on port 5000 and binds to all network interfaces.

### 4. Access from Your Desktop

Open a browser and go to:

```
http://localhost:5000
```

### 5. Access from Another Device on Your Network

First, find your desktop's local IP address:

**Windows:**
```
ipconfig
```
Look for "IPv4 Address" under your active network adapter (usually something like `192.168.1.x` or `192.168.0.x`).

**Linux/Mac:**
```
ip addr
# or
ifconfig
```
Look for the `inet` address on your active interface (e.g., `eth0`, `wlan0`, `en0`).

Then on your other device (tablet, laptop, phone), open a browser and go to:

```
http://<your-desktop-ip>:5000
```

For example: `http://192.168.1.100:5000`

### 6. Firewall

Your desktop's firewall must allow inbound connections on port 5000.

**Windows:**
```
netsh advfirewall firewall add rule name="FitLocal" dir=in action=allow protocol=TCP localport=5000
```

**Linux (ufw):**
```
sudo ufw allow 5000/tcp
```

### 7. Tip: Use a Static IP

To keep the bookmark stable on your workout room device, assign a static local IP to your desktop in your router's settings (often called "DHCP Reservation" or "Static Lease").

---

## Run on Startup

### Windows (Task Scheduler)

1. Open Task Scheduler (`taskschd.msc`)
2. Click "Create Basic Task"
3. Name it "FitLocal"
4. Trigger: "When the computer starts"
5. Action: "Start a program"
   - Program: `python` (or full path to `python.exe`)
   - Arguments: `app.py`
   - Start in: the full path to your `fitlocal/` folder
6. Finish. Right-click the task > Properties > check "Run whether user is logged on or not"

### Linux (systemd)

Create `/etc/systemd/system/fitlocal.service`:

```ini
[Unit]
Description=FitLocal Workout Trainer
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/fitlocal
ExecStart=/usr/bin/python3 app.py
Restart=on-failure
Environment=ANTHROPIC_API_KEY=your-key-here

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable fitlocal
sudo systemctl start fitlocal
```
