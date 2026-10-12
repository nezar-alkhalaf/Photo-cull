PhotoCull
=========

ملخص بالعربي
------------
1) أول مرة على أي جهاز: انقر مرتين على "First-Time Setup" وانتظر لين يكتب Done (يحتاج انترنت، وياخذ كم دقيقة).
2) بعدها شغّل البرنامج من أيقونة الكاميرا PhotoCull اللي في هذا المجلد (أو من سطح المكتب). تقدر تنسخ الأيقونة (Copy) وتلصقها (Paste) في سطح المكتب أو أي مكان.
3) اضغط "Choose folder" واختر أي مجلد فيه صورك (ما يحتاج اسم معين). صورك ما تتغيّر، والنتائج تنحفظ في مجلد جديد جنبه اسمه "اسم المجلد - PhotoCull".
4) اختر الستايل من القائمة (ملفات الـ PSD اللي في مجلد Styles) ثم اضغط "Run All".
5) زر «العربية / English» فوق في النافذة يغيّر لغة الواجهة، والبرنامج يتذكر اختيارك.
6) لو صار شي غريب: افتح صفحة الإعدادات (Settings) واضغط "Check this PC" في قسم Tools، يعطيك قائمة بالمشاكل وحلها.
7) الأزرار A− و A+ تحت يسار النافذة تصغّر وتكبّر الخط، والبرنامج يتذكر اختيارك.
8) لنقل البرنامج لجهاز ثاني: انسخ مجلد PhotoCull كامل (بدون مجلد .venv)، وشغّل First-Time Setup مرة وحدة هناك.


Quick start (English)
---------------------
1. First time on a PC: double-click "First-Time Setup" and wait until it says Done.
   It needs internet and takes a few minutes.
2. From then on, double-click the PhotoCull camera icon in this folder (or on the Desktop).
   You can Copy it and Paste it anywhere (Desktop, Taskbar...). It points to this folder, so
   if you move the folder, run First-Time Setup again to refresh it.
3. Click "Choose folder" and pick ANY folder that has your photos (no special name needed).
   PhotoCull never changes your photos: the results go to a new folder beside it called
   "<folder name> - PhotoCull". (A folder that has a Full_Coverage folder inside still works.)
4. Pick your style in the list (the .psd files in the Styles folder), then press "Run All".
   Or run the three steps one by one: 1 Cull, 2 Edit in Photoshop, 3 Split into posts.
5. The button at the top right (English / العربية) switches the window language. It is remembered.
6. Look at the picks in the review area. One click swaps a pick with an alternate.

What you will see in this folder
--------------------------------
  PhotoCull               the camera icon: the one thing to double-click
  First-Time Setup.bat    run once on every new PC (it also makes the PhotoCull icon)
  Styles                  your style files (.psd, and optionally .atn) go here
  README.txt              this page
(The file "Start PhotoCull.bat" is hidden on purpose: the PhotoCull icon uses it.)
Everything else lives in a hidden folder called "app". To see it: in File Explorer
open View > Show > Hidden items. You never need to open it, except to read the logs.

Where the results go (inside each coverage folder)
--------------------------------------------------
  Full_Coverage   your original photos (read only for PhotoCull)
  Selected        the best photo of each moment
  Alternates      the next best ones, in case you prefer another
  Edited          after the Photoshop style
  Posts           Post_1, Post_2 ... ready for Instagram
  report.csv      scores and reasons for every photo (opens in Excel)

How to make a style
-------------------
1. In Photoshop open one of your photos and build the look with ADJUSTMENT LAYERS only
   (Curves, Levels, Brightness/Contrast, Color Balance, Hue/Saturation ...).
   Put them in one group called STYLE.
2. Save it as a .psd inside the Styles folder (any name, for example Warm.psd).
3. In PhotoCull choose it in the style list. The list refreshes when you come back
   to the window.
An Action (.atn) is optional. If you record one, use only adjustment layers or
steps that do not open a dialog window (a dialog stops the automatic run), and
type its name in Settings > Photoshop > Photoshop Action as  SetName/ActionName.

Moving PhotoCull to another PC
------------------------------
1. Copy the whole PhotoCull folder to the new PC. Do NOT copy the ".venv" folder
   inside "app" (it is rebuilt there). Coverage folders can be copied the same way
   and will continue where they stopped.
2. On the new PC double-click "First-Time Setup" once, then use the PhotoCull icon.
3. The new PC needs Python 3.12 (python.org, tick "Add Python to PATH") and, for
   step 2, Photoshop. Steps 1 and 3 work without Photoshop.
4. If the last coverage folder is not found, press "Choose folder" and pick it again.

What to back up first
---------------------
  1. Your coverage folders, especially Full_Coverage (your originals).
  2. The Styles folder (your looks).
  3. app\settings.json (your last choices). Or use Settings > Tools > Export settings.
Nothing else is needed: the rest is rebuilt by First-Time Setup.

If something goes wrong
-----------------------
First: open the Settings page and press "Check this PC" (section Tools). Every line has a colour (green is fine)
and a "Fix" under each problem. "Copy report" puts the list in the clipboard to send.
The same check from a command window opened inside the app folder:
    .venv\Scripts\python -m photocull doctor

* "PhotoCull is not set up on this PC yet", or the icon does nothing after you moved the
  folder: run First-Time Setup again.
* Python not found by Setup: install Python 3.12 from python.org and tick
  "Add Python to PATH". Then run First-Time Setup again.
* Photoshop does not connect: close any message box open inside Photoshop, wait until
  it is fully started, and press Run again. Use Check this PC > "Test Photoshop
  connection". If it still fails, restart Photoshop once.
  If Check this PC says the Photoshop link (pywin32) is broken, run First-Time Setup
  again; if that does not help, run in a command window:
    .venv\Scripts\python .venv\Scripts\pywin32_postinstall.py -install
  (command window opened inside the app folder)
* Face detection (MediaPipe) fails to install or start: the internet may have cut out
  during Setup, so run First-Time Setup again. Without it the program still culls by
  sharpness and exposure but cannot judge closed eyes. If it still fails, send the
  Check this PC report to the developer.
* The window does not open: look in app\logs for crash.log (Settings > Tools > Open logs
  folder) and send it.
* Not enough disk space: every step writes a copy of the photos; free some space.
