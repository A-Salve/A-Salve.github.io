# A-Salve.github.io
Smart Deals Daily

## Python utility: font sender for printers

Added desktop utility `font_sender_app.py` (Tkinter + Paramiko) for sending selected fonts to a list of printers through SSH.

### Quick start

1. Install dependency:
   ```bash
   pip install paramiko
   ```
2. Fill `rc_list.txt` in format:
   ```text
   Название_РЦ;IP
   ```
3. Run app:
   ```bash
   python3 font_sender_app.py
   ```
