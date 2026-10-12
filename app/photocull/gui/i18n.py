"""English / Arabic text for the window.

Every visible string in the GUI goes through t("English text"); the English text is the
key. Text with variables uses {name} placeholders and is filled in by the caller:
    t("{n} selected").format(n=5)
Anything without an Arabic entry shows in English, so a missing line never breaks the window.
Messages that come from the core steps (cull / edit / split) are translated best-effort by
tr_msg(), which rewrites their known sentence shapes.
Works without a display (no tkinter import).
"""

from __future__ import annotations

import re

LANG = "en"
LANGUAGES = ("en", "ar")


def set_lang(lang: str) -> None:
    global LANG
    LANG = lang if lang in LANGUAGES else "en"


def is_ar() -> bool:
    return LANG == "ar"


def anchor() -> str:
    """Text anchor for left-to-right (w) or right-to-left (e) reading."""
    return "e" if LANG == "ar" else "w"


def justify() -> str:
    return "right" if LANG == "ar" else "left"


RLM = "‏"      # right-to-left mark: makes lines that start with a number read right-to-left


def side(s: str) -> str:
    """pack side: left <-> right when the window reads right-to-left."""
    return {"left": "right", "right": "left"}.get(s, s) if LANG == "ar" else s


def sticky(s: str) -> str:
    """grid sticky: w <-> e when the window reads right-to-left."""
    if LANG != "ar":
        return s
    return s.translate(str.maketrans("we", "ew"))


def px(a: int, b: int) -> tuple[int, int]:
    """(left, right) padding, swapped for right-to-left."""
    return (b, a) if LANG == "ar" else (a, b)


def mcol(i: int, n: int) -> int:
    """Column index i of n, counted from the right when the window reads right-to-left."""
    return n - 1 - i if LANG == "ar" else i


def t(text: str) -> str:
    if LANG == "ar" and text in AR:
        return RLM + AR[text]
    return text


AR: dict[str, str] = {
    # ---- header
    "Choose folder…": "اختر المجلد…",
    "No folder chosen": "ما تم اختيار مجلد",
    "The last folder is gone. Choose it again.": "المجلد الأخير مو موجود. اختره من جديد.",
    "Dark": "داكن",
    "Portrait (studio)": "بورتريه (استوديو)",
    "Action (matches)": "حركة (مباريات)",
    "Portrait: studio and class photos, eyes matter.\n"
    "Action: matches and events, sharpness matters most.":
        "بورتريه: صور الاستوديو والفصول، العيون مهمة.\n"
        "حركة: المباريات والفعاليات، الوضوح هو الأهم.",
    "Switch the window between English and Arabic": "تبديل لغة النافذة بين العربية والإنجليزية",
    # ---- Photoshop status
    "Photoshop is ready": "فوتوشوب جاهز",
    "Photoshop {v} is ready": "فوتوشوب {v} جاهز",
    "Photoshop is closed (it opens by itself, about 30 s)":
        "فوتوشوب مقفل (يفتح لحاله، حوالي 30 ثانية)",
    "Photoshop not found (steps 1 and 3 still work)":
        "فوتوشوب غير موجود (الخطوتين 1 و3 تشتغل)",
    "Photoshop link missing: run First-Time Setup again":
        "رابط فوتوشوب ناقص: شغّل First-Time Setup من جديد",
    "Checking Photoshop…": "جاري فحص فوتوشوب…",
    "Photoshop is being used by PhotoCull": "فوتوشوب يستخدمه البرنامج الحين",
    # ---- dashboard
    "Photos": "الصور",
    "Moments": "اللحظات",
    "Selected": "المختارة",
    "Alternates": "البدائل",
    "Rejected": "المستبعدة",
    "blurry": "ضبابية",
    "eyes": "عيون",
    "exposure": "إضاءة",
    "Run step 1 to see how many posts you will get.":
        "شغّل الخطوة 1 عشان تعرف كم بوست بيطلع.",
    "{sel} selected  →  {n} post  →  {sizes}": "{sel} مختارة  ←  {n} بوست  ←  {sizes}",
    "{sel} selected  →  {n} posts  →  {sizes}": "{sel} مختارة  ←  {n} بوستات  ←  {sizes}",
    "Type a whole number for “Max images per post”.":
        "اكتب رقم صحيح في «أقصى عدد صور في البوست».",
    # ---- settings bar
    "Style": "الستايل",
    "— choose a style —": "— اختر ستايل —",
    "Open Styles folder": "افتح مجلد الستايلات",
    "Max images per post": "أقصى عدد صور في البوست",
    "Group gap (s)": "الفاصل بين اللحظات (ث)",
    "Photos taken closer together than this many seconds count as one moment.":
        "الصور اللي بينها وقت أقل من هذا العدد من الثواني تُحسب لحظة وحدة.",
    "Fix exposure": "تصحيح الإضاءة",
    "Brightens dark photos before Photoshop. ON for night matches, OFF for studio.":
        "يفتّح الصور المظلمة قبل فوتوشوب. شغّله لمباريات الليل وأطفئه للاستوديو.",
    # ---- advanced
    "Blur threshold": "حد الضبابية",
    "Closed-eye threshold": "حد العيون المغمضة",
    "Alternates per moment": "بدائل لكل لحظة",
    "JPEG quality": "جودة JPEG",
    "Photoshop Action": "أكشن فوتوشوب",
    "Set/Action, optional": "مجموعة/أكشن، اختياري",
    "Preview first photos": "معاينة أول صور",
    "0 = off": "0 = إيقاف",
    "Skip blurry moments": "تخطي اللحظات الضبابية",
    "Cull reads from (empty = Full_Coverage)": "الاختيار يقرأ من (فاضي = Full_Coverage)",
    "Edit reads from (empty = Selected)": "التعديل يقرأ من (فاضي = Selected)",
    "Split reads from (empty = Edited)": "التقسيم يقرأ من (فاضي = Edited)",
    "Browse…": "استعراض…",
    "Open logs folder": "افتح مجلد السجلات",
    "Check this PC": "افحص هذا الجهاز",
    "Reset advanced values": "إعادة القيم الافتراضية",
    "Export settings…": "تصدير الإعدادات…",
    "Import settings…": "استيراد الإعدادات…",
    "Save my settings": "احفظ إعداداتي",
    "Settings": "الإعدادات",
    "Could not save the settings:\n{exc}": "ما قدرت أحفظ الإعدادات:\n{exc}",
    "Load settings from a file": "حمّل الإعدادات من ملف",
    "This is not a usable settings file:\n{exc}": "هذا ملف إعدادات ما يصلح:\n{exc}",
    "Settings loaded. Close and open PhotoCull again to see them all.":
        "تم تحميل الإعدادات. سكّر البرنامج وافتحه من جديد عشان تظهر كلها.",
    "Choose a folder": "اختر مجلد",
    # ---- run buttons and step cards
    "▶  Run All": "▶  شغّل الكل",
    "1. Cull": "1 · الاختيار",
    "Pick the best photos": "اختر أحسن الصور",
    "Reads Full_Coverage, keeps the best frame of each moment.":
        "يقرأ Full_Coverage ويحتفظ بأحسن لقطة من كل لحظة.",
    "2. Edit in Photoshop": "2 · فوتوشوب",
    "Edit photos": "عدّل الصور",
    "Reads Selected, applies your style, saves to Edited.":
        "يقرأ Selected ويطبق الستايل ويحفظ في Edited.",
    "3. Split into posts": "3 · التقسيم",
    "Split photos": "قسّم الصور",
    "Reads Edited, copies into Post_1, Post_2… folders.":
        "يقرأ Edited وينسخ الصور لمجلدات Post_1 وPost_2…",
    "Cull": "الاختيار",
    "Edit in Photoshop": "التعديل في فوتوشوب",
    "Split into posts": "التقسيم لبوستات",
    "Run All": "شغّل الكل",
    "Open folder": "افتح المجلد",
    "Not created yet: run this step first.": "لسا ما انعمل: شغّل هذي الخطوة أول.",
    "Not started": "ما بدأت",
    "Not finished": "ما خلصت",
    "Partial": "جزئي",
    "Done ✓": "تمت ✓",
    "Queued": "بالانتظار",
    "Running…": "جاري التشغيل…",
    "Preview ready": "المعاينة جاهزة",
    "Stopped": "متوقفة",
    "Error": "خطأ",
    # ---- why a button is disabled
    "Wait for the current step to finish, or press Stop.":
        "انتظر لين تخلص الخطوة الحالية، أو اضغط إيقاف.",
    "Choose a main folder first.": "اختر المجلد الرئيسي أول.",
    "No photos found. Copy the photos into the Full_Coverage folder first.":
        "ما لقيت صور. انسخ الصور لمجلد Full_Coverage أول.",
    "No photos in the folder chosen under Settings → Folders → Cull reads from.":
        "ما فيه صور في المجلد المختار في الإعدادات ← المجلدات ← الاختيار يقرأ من.",
    "Choose a style first (a .psd file in the Styles folder).":
        "اختر ستايل أول (ملف .psd في مجلد Styles).",
    "Photoshop is not installed on this PC.": "فوتوشوب غير مثبت على هذا الجهاز.",
    "The Photoshop link is missing. Run First-Time Setup again.":
        "رابط فوتوشوب ناقص. شغّل First-Time Setup من جديد.",
    "Nothing to edit yet. Run step 1 first.": "ما فيه شي للتعديل. شغّل الخطوة 1 أول.",
    "Nothing to split yet. Run step 1 (or step 2) first.":
        "ما فيه شي للتقسيم. شغّل الخطوة 1 (أو 2) أول.",
    "There are no edited photos yet (step 2).\n\nSplit the {n} picked photos as they are, without Photoshop editing?":
        "ما فيه صور معدّلة للحين (الخطوة 2).\n\nأقسّم الصور المختارة ({n}) كما هي بدون تعديل فوتوشوب؟",
    "Split the picked photos": "قسّم الصور المختارة",
    "Nothing to split yet. Run step 2 first.": "ما فيه شي للتقسيم. شغّل الخطوة 2 أول.",
    "Photoshop is not ready, so step 2 cannot run.": "فوتوشوب مو جاهز، فالخطوة 2 ما تشتغل.",
    "Wait for the current step to finish.": "انتظر لين تخلص الخطوة الحالية.",
    "This moment has no alternates.": "هذي اللحظة ما لها بدائل.",
    "It is the only one, use the button above.": "هي الوحيدة، استخدم الزر اللي فوق.",
    # ---- review area
    "Selected ({n})": "المختارة ({n})",
    "Alternates ({n})": "البدائل ({n})",
    "Click a photo to see why it was picked. Double-click opens it.":
        "اضغط على صورة تشوف ليش انختارت. ضغطتين تفتحها.",
    "your pick": "اختيارك",
    "check me": "راجعني",
    "No photos here yet. Run step 1 (Cull).": "ما فيه صور هنا. شغّل الخطوة 1 (الاختيار).",
    "No alternates (they appear after Cull).": "ما فيه بدائل (تظهر بعد الاختيار).",
    "Nothing here yet.": "ما فيه شي هنا.",
    "Select a photo": "اختر صورة",
    "loading…": "جاري التحميل…",
    "(cannot show this photo)": "(ما أقدر أعرض هذي الصورة)",
    "Moment {g}: {size} photo(s), this one ranks #{rank}":
        "اللحظة {g}: {size} صور، وهذي ترتيبها {rank}",
    "Sharpness {sharp}   Brightness {bright}   Score {score}":
        "الوضوح {sharp}   السطوع {bright}   النتيجة {score}",
    "Face: {face}   Eyes: {eyes}": "وجه: {face}   عيون: {eyes}",
    "Notes: ": "ملاحظات: ",
    "The program was not sure about this moment. Please check it.":
        "البرنامج مو متأكد من هذي اللحظة. تكرّم راجعها.",
    "No score: this photo was not made by the Cull step.":
        "ما فيه نتيجة: هذي الصورة ما جت من خطوة الاختيار.",
    "looks blurry": "تبدو ضبابية",
    "eyes closed": "العيون مغمضة",
    "too bright": "ساطعة زيادة",
    "too dark": "مظلمة زيادة",
    "flat, low contrast": "باهتة وتباينها ضعيف",
    "another frame of this moment is better": "فيه لقطة ثانية من نفس اللحظة أحسن",
    "you chose this one": "انت اخترت هذي",
    "yes": "نعم",
    "no": "لا",
    "open": "مفتوحة",
    "closed": "مغمضة",
    "Swap with another frame of this moment": "بدّلها بلقطة ثانية من نفس اللحظة",
    "Remove from Selected": "شيلها من المختارة",
    "Use this instead of the pick": "استخدمها بدل المختارة",
    "Add to Selected": "أضفها للمختارة",
    "Also add to Selected": "أضفها للمختارة بعد",
    "Could not change the pick:\n{exc}": "ما قدرت أغيّر الاختيار:\n{exc}",
    "Edited photo": "صورة معدّلة",
    "{names} was already edited, but is no longer picked. Delete the edited copy so it "
    "does not end up in your posts?\n(Photos in Full_Coverage and Alternates are not "
    "touched.)":
        "{names} انعدلت من قبل، لكنها ما عادت مختارة. أحذف النسخة المعدّلة عشان ما تدخل "
        "في بوستاتك؟\n(الصور في Full_Coverage والبدائل ما تنلمس.)",
    "Delete the edited copy": "احذف النسخة المعدّلة",
    "Keep it": "خلّها",
    "Pick changed. Run step 2 again to edit the new photo.":
        "تغيّر الاختيار. شغّل الخطوة 2 من جديد عشان تتعدل الصورة الجديدة.",
    "Choose the photo to use": "اختر الصورة اللي تبيها",
    "Click the photo you want, then press Use this photo.":
        "اضغط على الصورة اللي تبيها، وبعدها اضغط «استخدم هذي الصورة».",
    "Use this photo": "استخدم هذي الصورة",
    # ---- footer / progress
    "Stop": "إيقاف",
    "Ready.": "جاهز.",
    "Starting…": "جاري البدء…",
    "Starting… (Photoshop can take up to 30 seconds to open)":
        "جاري البدء… (فوتوشوب ممكن ياخذ 30 ثانية يفتح)",
    "Stopping after the current photo…": "بيوقف بعد الصورة الحالية…",
    "Stopping after the current photo, then closing…":
        "بيوقف بعد الصورة الحالية، وبعدها يسكّر…",
    "Step {i}/3 · {title}  —  ": "الخطوة {i}/3 · {title}  —  ",
    "Done.": "تمت.",
    "Something went wrong.": "صار خطأ.",
    "  …and {n} more (see the log).": "  …و{n} أخرى (شوف السجل).",
    "Something went wrong in the window: {err}. Details are in the logs folder.":
        "صار خطأ في النافذة: {err}. التفاصيل في مجلد السجلات.",
    # ---- dialogs
    "Choose the main folder of this coverage": "اختر المجلد الرئيسي لهذي التغطية",
    "This folder has no Full_Coverage folder yet.\n\nCreate it now? Then copy all the "
    "photos of the coverage into it.":
        "هذا المجلد ما فيه مجلد Full_Coverage.\n\nأسويه الحين؟ بعدها انسخ كل صور التغطية فيه.",
    "Could not create the folder:\n{exc}": "ما قدرت أسوي المجلد:\n{exc}",
    "Could not open {path}\n\n{exc}": "ما قدرت أفتح {path}\n\n{exc}",
    "A step is still running.\n\nStop it and close the window?":
        "فيه خطوة شغالة.\n\nأوقفها وأسكّر النافذة؟",
    "Selected and Alternates already hold {n} photo(s) from an earlier run.\n\nReplace "
    "them with the new picks? (The photos in Full_Coverage are never touched.)":
        "المختارة والبدائل فيها {n} صور من تشغيل سابق.\n\nأبدلها بالاختيار الجديد؟ "
        "(صور Full_Coverage ما تنلمس أبداً.)",
    "Replace them": "بدّلها",
    "{n} photo(s) were already edited with different settings (style, action or "
    "exposure).\n\nEdit them again with the new settings? The old edited copies will be "
    "replaced.":
        "{n} صور انعدلت من قبل بإعدادات ثانية (ستايل أو أكشن أو إضاءة).\n\nأعدّلها من جديد "
        "بالإعدادات الجديدة؟ النسخ المعدّلة القديمة بتنبدل.",
    "Edit them again": "عدّلها من جديد",
    "Posts already has {n} post folder(s).\n\nDelete the old Post_N folders and start "
    "again, or add new posts after them?":
        "مجلد Posts فيه {n} مجلدات بوست.\n\nأحذف مجلدات Post_N القديمة وأبدأ من جديد، "
        "أو أضيف بوستات جديدة بعدها؟",
    "Delete the old posts": "احذف البوستات القديمة",
    "Add new posts": "أضف بوستات جديدة",
    "Keep them and continue": "خلّها وكمّل",
    "Cancel": "إلغاء",
    "{message}\n\nOpen the Edited folder and check how the photos look. Continue with the "
    "rest?":
        "{message}\n\nافتح مجلد Edited وشوف كيف طلعت الصور. أكمل الباقي؟",
    "Open Edited folder": "افتح مجلد Edited",
    "Continue": "كمّل",
    "Stop here": "وقّف هنا",
    "Photoshop Action: {exc}": "أكشن فوتوشوب: {exc}",
    # ---- doctor window
    "Checking…": "جاري الفحص…",
    "Starting Photoshop and testing… (up to 30 s)": "جاري تشغيل فوتوشوب والفحص… (لين 30 ثانية)",
    "Check again": "افحص من جديد",
    "Test Photoshop connection": "جرّب الاتصال بفوتوشوب",
    "Copy report": "انسخ التقرير",
    "Close": "إغلاق",
    "Fix: ": "الحل: ",
    "Home":
        "الرئيسية",
    "Review photos":
        "مراجعة الصور",
    "Settings":
        "الإعدادات",
    "Activity":
        "السجل",
    "Coverage folder":
        "مجلد التغطية",
    "Options":
        "الخيارات",
    "Run":
        "التشغيل",
    "Summary":
        "الملخص",
    "Type of shoot":
        "نوع التصوير",
    "Pick any folder that has your photos. They are never changed: the results are saved in a new folder next to it.":
        "اختر أي مجلد فيه صورك. صورك ما تتغيّر أبداً، والنتائج تنحفظ في مجلد جديد جنبه.",
    "Photos: {src}\nResults go to: {main}": "الصور: {src}\nالنتائج في: {main}",
    "This folder has no photos yet.\n\nCreate a Full_Coverage folder in it now? Then copy the photos of the coverage into it.":
        "هذا المجلد ما فيه صور.\n\nأسوي فيه مجلد Full_Coverage الحين؟ بعدها انسخ فيه صور التغطية.",
    "No photos found. Choose a folder that has photos in it.":
        "ما لقيت صور. اختر مجلد فيه صور.",
    "Pick the folder of this coverage. It must hold a Full_Coverage folder with all the photos.":
        "اختر مجلد هذي التغطية. لازم يكون فيه مجلد Full_Coverage وفيه كل الصور.",
    "Review the picked photos →":
        "راجع الصور المختارة ←",
    "Check what the program picked. You can swap any photo with another frame of the same moment.":
        "شوف وش اختار البرنامج. تقدر تبدّل أي صورة بلقطة ثانية من نفس اللحظة.",
    "Most people never need to change these. The normal values work well. An empty box means the normal value (written under each setting).":
        "القيم العادية تكفي أغلب الناس. الخانة الفاضية تعني القيمة العادية.",
    "Normal value: {v}": "القيمة العادية: {v}",
    "Grouping photos into moments":
        "تجميع الصور في لحظات",
    "How many runner-up photos to keep next to each pick.":
        "كم صورة بديلة تنحفظ مع كل اختيار.",
    "If even the sharpest photo of a moment is blurry, leave the whole moment out.":
        "إذا حتى أوضح صورة في اللحظة ضبابية، اترك اللحظة كلها.",
    "Spotting weak photos":
        "كشف الصور الضعيفة",
    "Photos sharper than this count as sharp. Higher = stricter.":
        "الصور الأوضح من هذا الرقم تُحسب واضحة. كل ما زاد الرقم زاد التشدد.",
    "Eyes narrower than this count as closed. Higher = stricter.":
        "العيون الأضيق من هذا الرقم تُحسب مغمضة. كل ما زاد الرقم زاد التشدد.",
    "Photoshop":
        "فوتوشوب",
    "Quality of the edited photos saved by Photoshop (1 to 100).":
        "جودة الصور المعدّلة اللي يحفظها فوتوشوب (من 1 إلى 100).",
    "Optional: an Action to run after the style, written Set/Action.":
        "اختياري: أكشن يشتغل بعد الستايل، يُكتب هكذا مجموعة/أكشن.",
    "Edit only this many photos, then pause so you can check the look. Empty = off.":
        "يعدّل هذا العدد من الصور بس ويوقف عشان تشوف الشكل. فاضي = إيقاف.",
    "Folders":
        "المجلدات",
    "Leave these empty to use the normal folders. Change them only to work on photos kept somewhere else.":
        "خلّها فاضية عشان تستخدم المجلدات العادية. غيّرها بس إذا صورك في مكان ثاني.",
    "Tools":
        "أدوات",
    "Clear":
        "امسح",
    "What PhotoCull did, step by step. Warnings and errors are in red.":
        "وش سوّى البرنامج خطوة بخطوة. التنبيهات والأخطاء بالأحمر.",
    "Details ›":
        "التفاصيل ›",
    "Smaller text": "تصغير الخط",
    "Bigger text": "تكبير الخط",
}

# Sentence shapes produced by the core steps (cull / edit / split / pipeline), rewritten in
# order; whatever does not match stays in English.
_MSG: list[tuple[re.Pattern, str]] = [(re.compile(p), r) for p, r in (
    (r"All three steps finished\. ", "خلصت الخطوات الثلاث. "),
    (r"Step (\d) \((.+?)\) did not finish\. ", r"الخطوة \1 (\2) ما خلصت. "),
    (r" Run All paused here\.", " توقف «شغّل الكل» هنا."),
    (r"Kept the earlier results\.", "خليت النتائج السابقة."),
    (r"Nothing was changed: the earlier results are still there\.",
     "ما تغيّر شي: النتائج السابقة باقية."),
    (r"Stopped while copying\. ", "توقف أثناء النسخ. "),
    (r" Output is incomplete; run again\.", " النتيجة ناقصة، شغّلها من جديد."),
    (r"(\d+) photos, (\d+) groups: (\d+) selected, (\d+) alternates",
     r"\1 صور، \2 لحظة: \3 مختارة، \4 بدائل"),
    (r", (\d+) unreadable", r"، \1 غير مقروءة"),
    (r", (\d+) blurry group\(s\) skipped", r"، تم تخطي \1 لحظة ضبابية"),
    (r", (\d+) weak group\(s\) to review", r"، \1 لحظة تحتاج مراجعة"),
    (r" (\d+) copy error\(s\); check the log\.", r" \1 أخطاء نسخ، شوف السجل."),
    (r"Copied (\d+) of (\d+) into (\d+) post\(s\): ", r"نُسخت \1 من \2 في \3 بوست: "),
    (r"Copied (\d+) of (\d+); verified (\d+)\. Check the log\.",
     r"نُسخت \1 من \2، والمتحقق منها \3. شوف السجل."),
    (r"Stopped\. Copied (\d+) of (\d+)\.", r"توقف. نُسخت \1 من \2."),
    (r"Stopped\. Edited (\d+) of (\d+); run again to continue\.",
     r"توقف. تعدلت \1 من \2، شغّلها من جديد عشان تكمل."),
    (r"Stopped\. Analysed (\d+) of (\d+);", r"توقف. تم تحليل \1 من \2؛"),
    (r"Edited (\d+) of (\d+); (\d+) failed \(see the log\)\. Run again to retry them\.",
     r"تعدلت \1 من \2، وفشلت \3 (شوف السجل). شغّلها من جديد عشان تعيد المحاولة."),
    (r"Edited (\d+) of (\d+) with (.+?)\.$", r"تعدلت \1 من \2 بستايل \3."),
    (r"All (\d+) photos were already edited\.", r"كل الصور (\1) انعدلت من قبل."),
    (r"Preview ready: (\d+) photo\(s\) in Edited\. Check them, then continue to edit the "
     r"other (\d+)\.", r"المعاينة جاهزة: \1 صور في Edited. شوفها، وبعدها كمّل تعديل الباقي (\2)."),
    (r"No photos found in (.+?); nothing to do\.", r"ما لقيت صور في \1، ما فيه شي أسويه."),
    (r"Input folder not found: ", "مجلد المدخلات مو موجود: "),
    (r"Cannot continue: ", "ما أقدر أكمل: "),
    (r"Stopped\.$", "توقف."),
    (r"Max images per post must be at least 1\.", "أقصى عدد صور في البوست لازم يكون 1 على الأقل."),
    (r"Selected/Alternates already hold (\d+) photo\(s\) ",
     r"المختارة/البدائل فيها \1 صور "),
    (r"Posts already has (\d+) post folder\(s\)\. ", r"مجلد Posts فيه \1 مجلدات بوست. "),
    (r"Put a \.psd in the Styles folder and choose it\.",
     "حط ملف .psd في مجلد Styles واختره."),
    (r"Unexpected error: ", "خطأ غير متوقع: "),
    (r"All green\. This PC is ready\.", "كله أخضر. هذا الجهاز جاهز."),
    (r"Everything needed works\. (\d+) warning\(s\) above\.",
     r"كل المطلوب يشتغل. فيه \1 تنبيه فوق."),
    (r"(\d+) problem\(s\) to fix", r"\1 مشكلة تحتاج حل"),
    (r", (\d+) warning\(s\)\.", r"، و\1 تنبيه."),
)]


def tr_msg(text: str) -> str:
    """Best-effort Arabic for a message written by the core steps."""
    if LANG != "ar" or not text:
        return text
    out = text
    for pat, repl in _MSG:
        out = pat.sub(repl, out)
    return RLM + out if out != text else text
