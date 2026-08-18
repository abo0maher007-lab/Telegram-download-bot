import time
import re
import asyncio
from telethon import events, Button
from config import VERSION, ACTIVE_CANCEL_EVENTS
from database import get_user_config, set_user_config, save_task, pop_task
from utils import get_clean_filename, is_x_url, is_complex_url
from downloader import start_direct_execution

def build_settings_buttons(chat_id):
    config = get_user_config(chat_id)
    snap_status = "✅ مفعّلة" if config["snapshots"] else "❌ معطلة"
    
    font_labels = {"small": "متوسط", "medium": "كبير جداً", "large": "ضخم", "xlarge": "عملاق"}
    
    return [
        [Button.inline(f"📸 لقطات الفيديو: {snap_status}", data="toggle_snaps")],
        [Button.inline(f"🎥 الجودة: {config['quality']}p", data="change_qual"), Button.inline(f"🔤 الخط: {font_labels.get(config['font_size'], 'ضخم')}", data="change_font")],
        [Button.inline("❌ إغلاق اللوحة", data="close_settings")]
    ]

def register_handlers(bot):

    @bot.on(events.CallbackQuery(pattern=r"^cancel_"))
    async def cancel_callback_handler(event):
        data = event.data.decode("utf-8").split("_")
        task_id = "_".join(data[1:])
        if task_id in ACTIVE_CANCEL_EVENTS:
            ACTIVE_CANCEL_EVENTS[task_id].set()
            await event.answer("🛑 جاري إلغاء العملية...", alert=True)

    @bot.on(events.NewMessage(pattern=r"^/settings$"))
    async def settings_handler(event):
        chat_id = event.chat_id
        await event.respond("⚙️ **لوحة التحكم والإعدادات الخاصّة بالبوت:**", buttons=build_settings_buttons(chat_id))

    @bot.on(events.CallbackQuery(pattern=r"^(toggle_snaps|change_qual|change_font|close_settings)$"))
    async def settings_callback_handler(event):
        chat_id = event.chat_id
        data = event.data.decode("utf-8")
        config = get_user_config(chat_id)

        if data == "toggle_snaps":
            config["snapshots"] = not config["snapshots"]
            set_user_config(chat_id, config)
            await event.answer("تم تغيير حالة ألبوم اللقطات!")
        elif data == "change_qual":
            qualities = ["480", "720", "1080"]
            idx = qualities.index(config["quality"]) if config["quality"] in qualities else 1
            config["quality"] = qualities[(idx + 1) % len(qualities)]
            set_user_config(chat_id, config)
            await event.answer(f"تم اختيار الجودة: {config['quality']}p")
        elif data == "change_font":
            fonts = ["small", "medium", "large", "xlarge"]
            idx = fonts.index(config["font_size"]) if config["font_size"] in fonts else 2
            config["font_size"] = fonts[(idx + 1) % len(fonts)]
            set_user_config(chat_id, config)
            await event.answer("تم تعديل حجم الخط!")
        elif data == "close_settings":
            await event.delete()
            return

        await event.edit("⚙️ **لوحة التحكم والإعدادات الخاصّة بالبوت:**", buttons=build_settings_buttons(chat_id))

    # أمر قص الفيديو حسب الدقائق والثواني
    @bot.on(events.NewMessage(pattern=r"^/trim\s+(\S+)\s+(\S+)\s+(https?://\S+)"))
    async def trim_handler(event):
        start_t = event.pattern_match.group(1)
        end_t = event.pattern_match.group(2)
        url = event.pattern_match.group(3)
        chat_id = event.chat_id

        user_config = get_user_config(chat_id)
        status_msg = await event.respond(f"✂️ **جاري تحضير قص المقطع من ({start_t}) إلى ({end_t})...**")

        asyncio.create_task(
            start_direct_execution(
                bot=bot, chat_id=chat_id, url=url, filename=get_clean_filename(url),
                quality=user_config["quality"], status_msg=status_msg, trim_times=(start_t, end_t)
            )
        )

    @bot.on(events.NewMessage(pattern=r"^/start$"))
    async def start_handler(event):
        welcome_text = (
            "🤖 **أهلاً بك في بوت التنزيل المباشر والشامل!**\n\n"
            "✂️ **لقص الفيديو بالدقائق والثواني:**\n"
            "أرسل: `/trim [توقيت البداية] [توقيت النهاية] [الرابط]`\n"
            "مثال: `/trim 00:01:00 00:02:30 https://example.com/video.mp4`\n\n"
            "⚙️ لتعديل الإعدادات أرسل: `/settings`"
        )
        await event.respond(welcome_text)

    @bot.on(events.NewMessage(pattern=r"(https?://\S+)"))
    async def url_handler(event):
        if event.text.startswith("/trim"): return
        urls = re.findall(r"https?://\S+", event.text)
        if not urls: return
        chat_id = event.chat_id
        
        for u in urls:
            clean_u = u.split('?')[0] if "instagram.com" in u else u
            if is_x_url(clean_u):
                task_key = f"x_{chat_id}_{int(time.time()*1000)}"
                save_task(task_key, clean_u, "x")
                buttons = [
                    [Button.inline("🎬 عالية (1080p)", data=f"q_1080_{task_key}"), Button.inline("🎥 متوسطة (720p)", data=f"q_720_{task_key}")],
                    [Button.inline("📱 منخفضة (480p)", data=f"q_480_{task_key}"), Button.inline("🎵 صوت فقط (MP3)", data=f"q_mp3_{task_key}")]
                ]
                await event.respond("🎬 **اختر جودة الفيديو المطلوبة لمنصة X:**", buttons=buttons)
            elif not is_complex_url(clean_u):
                task_key = f"dir_{chat_id}_{int(time.time()*1000)}"
                save_task(task_key, clean_u, "direct")
                buttons = [[
                    Button.inline("🎬 MP4", data=f"dir_mp4_{task_key}"),
                    Button.inline("🎵 MP3", data=f"dir_mp3_{task_key}"),
                    Button.inline("📄 مستند", data=f"dir_doc_{task_key}")
                ]]
                await event.respond("📌 **تم رصد رابط مباشر. اختر صيغة التحميل المناسبة:**", buttons=buttons)
            else:
                user_config = get_user_config(chat_id)
                asyncio.create_task(
                    start_direct_execution(bot=bot, chat_id=chat_id, url=clean_u, filename=get_clean_filename(clean_u), quality=user_config["quality"])
                )

    @bot.on(events.CallbackQuery(pattern=r"^q_"))
    async def quality_callback_handler(event):
        data = event.data.decode("utf-8").split("_")
        quality_choice = data[1]
        task_key = "_".join(data[2:])
        url, _ = pop_task(task_key)
        if not url:
            await event.answer("⚠️ انتهت صلاحية هذا الخيار.", alert=True)
            return
            
        chat_id = event.chat_id
        target_fmt = 'mp3' if quality_choice == 'mp3' else 'mp4'
        quality_val = 'best' if quality_choice == '1080' else quality_choice
        
        # ربط الشاشة لمنع اختفاء نافذة التحضير
        status_msg = await event.edit("⏳ **تم استلام طلبك، جاري بدء التنزيل...**", buttons=None)
        
        asyncio.create_task(
            start_direct_execution(
                bot=bot, chat_id=chat_id, url=url, filename=get_clean_filename(url),
                quality=quality_val, target_fmt=target_fmt, status_msg=status_msg
            )
        )

    @bot.on(events.CallbackQuery(pattern=r"^dir_"))
    async def direct_callback_handler(event):
        data = event.data.decode("utf-8").split("_")
        choice = data[1]
        task_key = "_".join(data[2:])
        url, _ = pop_task(task_key)
        if not url:
            await event.answer("⚠️ انتهت صلاحية هذا الخيار.", alert=True)
            return
            
        chat_id = event.chat_id
        status_msg = await event.edit("⏳ **جاري بدء التنزيل المباشر...**", buttons=None)
        
        asyncio.create_task(
            start_direct_execution(
                bot=bot, chat_id=chat_id, url=url, filename=get_clean_filename(url),
                as_doc=(choice == 'doc'), target_fmt=(choice if choice in ['mp4', 'mp3'] else 'mp4'), status_msg=status_msg
            )
        )        await send_settings_menu(event)

    @bot.on(events.CallbackQuery(pattern=r"^(toggle_snaps|toggle_social_snaps|change_qual|change_font|close_settings)$"))
    async def settings_callback_handler(event):
        chat_id = event.chat_id
        data = event.data.decode("utf-8")
        config = get_user_config(chat_id)

        if data == "toggle_snaps":
            config["snapshots"] = not config["snapshots"]
            set_user_config(chat_id, config)
            await event.answer("تم تغيير حالة ألبوم اللقطات العام!")
        elif data == "toggle_social_snaps":
            config["social_snapshots"] = not config["social_snapshots"]
            set_user_config(chat_id, config)
            await event.answer("تم تعديل لقطات منصات التواصل!")
        elif data == "change_qual":
            qualities = ["480", "720", "1080"]
            current_idx = qualities.index(config["quality"]) if config["quality"] in qualities else 1
            config["quality"] = qualities[(current_idx + 1) % len(qualities)]
            set_user_config(chat_id, config)
            await event.answer(f"الجودة: {config['quality']}p")
        elif data == "change_font":
            fonts = ["small", "medium", "large", "xlarge"]
            current_idx = fonts.index(config["font_size"]) if config["font_size"] in fonts else 2
            config["font_size"] = fonts[(current_idx + 1) % len(fonts)]
            set_user_config(chat_id, config)
            await event.answer("تم تعديل حجم الخط!")
        elif data == "close_settings":
            await event.delete()
            return

        await send_settings_menu(event, edit_msg=event)

    @bot.on(events.NewMessage(pattern=r"^/trim\s+(\S+)\s+(\S+)\s+(https?://\S+)"))
    async def trim_handler(event):
        start_t = event.pattern_match.group(1)
        end_t = event.pattern_match.group(2)
        url = event.pattern_match.group(3)
        chat_id = event.chat_id

        user_config = get_user_config(chat_id)
        status_msg = await event.respond(f"✂️ **جاري تحضير قص المقطع من ({start_t}) إلى ({end_t})...**")

        asyncio.create_task(
            start_direct_execution(
                bot, chat_id=chat_id, url=url, filename=get_clean_filename(url),
                as_doc=False, quality=user_config["quality"], target_fmt='mp4',
                status_msg=status_msg, trim_times=(start_t, end_t)
            )
        )

    @bot.on(events.NewMessage(pattern=r"^/start$"))
    async def start_handler(event):
        welcome_text = (
            "🤖 **أهلاً بك في بوت التنزيل المباشر والشامل!**\n\n"
            "⚙️ لتعديل الإعدادات أرسل: `/settings`"
        )
        buttons = [[Button.inline("📜 سجل التحديثات", data="show_changelog")]]
        await event.respond(welcome_text, buttons=buttons)

    @bot.on(events.CallbackQuery(pattern=r"^(show_changelog|close_changelog)$"))
    async def changelog_callback_handler(event):
        data = event.data.decode("utf-8")
        if data == "show_changelog":
            changelog_text = f"📜 **سجل التحديثات ({VERSION}):**\n1️⃣ كود مجزأ ومعياري لمشروع GitHub."
            await event.respond(changelog_text, buttons=[[Button.inline("❌ إغلاق", data="close_changelog")]])
        elif data == "close_changelog":
            await event.delete()

    @bot.on(events.NewMessage(pattern=r"(https?://\S+)"))
    async def url_handler(event):
        if event.text.startswith("/trim"): return
        urls = re.findall(r"https?://\S+", event.text) if 're' in globals() else [event.text.strip()]
        chat_id = event.chat_id
        
        for u in urls:
            clean_u = u.split('?')[0] if is_instagram_url(u) else u
            if is_x_url(clean_u):
                task_key = f"x_{chat_id}_{int(time.time()*1000)}"
                save_task(task_key, clean_u, "x")
                buttons = [
                    [Button.inline("🎬 عالية (1080p)", data=f"q_1080_{task_key}"), Button.inline("🎥 متوسطة (720p)", data=f"q_720_{task_key}")],
                    [Button.inline("📱 منخفضة (480p)", data=f"q_480_{task_key}"), Button.inline("🎵 صوت فقط (MP3)", data=f"q_mp3_{task_key}")]
                ]
                await event.respond("🎬 **اختر جودة الفيديو المطلوبة لمنصة X:**", buttons=buttons)
            elif not is_complex_url(clean_u):
                task_key = f"dir_{chat_id}_{int(time.time()*1000)}"
                save_task(task_key, clean_u, "direct")
                buttons = [[Button.inline("🎬 MP4", data=f"dir_mp4_{task_key}"), Button.inline("🎵 MP3", data=f"dir_mp3_{task_key}"), Button.inline("📄 مستند", data=f"dir_doc_{task_key}")]]
                await event.respond("📌 **تم رصد رابط مباشر. اختر الصيغة:**", buttons=buttons)
            else:
                user_config = get_user_config(chat_id)
                asyncio.create_task(start_direct_execution(bot, chat_id=chat_id, url=clean_u, filename=get_clean_filename(clean_u), as_doc=False, quality=user_config["quality"]))

    @bot.on(events.CallbackQuery(pattern=r"^(q_|dir_)"))
    async def selection_callback_handler(event):
        data = event.data.decode("utf-8").split("_")
        prefix, choice = data[0], data[1]
        task_key = "_".join(data[2:])
        url, _ = pop_task(task_key)
        if not url:
            await event.answer("⚠️ انتهت صلاحية هذا الخيار.", alert=True)
            return

        chat_id = event.chat_id
        status_msg = await event.edit("⏳ **جاري بدء الطلب...**", buttons=None)
        
        target_fmt = 'mp3' if choice in ['mp3'] else 'mp4'
        as_doc = (choice == 'doc')
        qual = choice if choice in ['480', '720', '1080'] else 'best'

        asyncio.create_task(
            start_direct_execution(
                bot, chat_id=chat_id, url=url, filename=get_clean_filename(url),
                as_doc=as_doc, quality=qual, target_fmt=target_fmt, status_msg=status_msg
            )
        )

async def send_settings_menu(event_or_msg, edit_msg=None):
    chat_id = event_or_msg.chat_id
    config = get_user_config(chat_id)
    buttons = [
        [Button.inline(f"📸 لقطات الفيديو: {'✅' if config['snapshots'] else '❌'}", data="toggle_snaps")],
        [Button.inline(f"🌐 لقطات التواصل: {'✅' if config['social_snapshots'] else '❌'}", data="toggle_social_snaps")],
        [Button.inline(f"🎥 الجودة: {config['quality']}p", data="change_qual"), Button.inline(f"🔤 الخط: {config['font_size']}", data="change_font")],
        [Button.inline("❌ إغلاق اللوحة", data="close_settings")]
    ]
    msg_text = "⚙️ **لوحة التحكم والإعدادات الخاصّة بالبوت:**"
    if edit_msg:
        await edit_msg.edit(msg_text, buttons=buttons)
    else:
        await event_or_msg.respond(msg_text, buttons=buttons)

async def start_direct_execution(bot, chat_id, url, filename, as_doc=False, quality='best', media_msg=None, target_fmt='mp4', status_msg=None, trim_times=None):
    task_id = f"task_{int(time.time() * 1000)}"
    cancel_event = threading.Event()
    ACTIVE_CANCEL_EVENTS[task_id] = cancel_event
    cancel_btn = [Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]

    if not status_msg: status_msg = await bot.send_message(chat_id, "⏳ **جاري التحضير...**", buttons=cancel_btn)
    else: await status_msg.edit("⏳ **جاري التحضير...**", buttons=cancel_btn)

    task_dir = os.path.join("downloads", task_id)
    os.makedirs(task_dir, exist_ok=True)
    user_config = get_user_config(chat_id)

    try:
        loop = asyncio.get_event_loop()
        target_url = clean_url(url) if is_complex_url(url) else url
        is_social = is_complex_url(target_url) if target_url else False

        if target_url and is_social:
            await loop.run_in_executor(None, download_with_ytdlp, target_url, task_dir, target_fmt, quality)
            if is_instagram_url(target_url) and not os.listdir(task_dir):
                await loop.run_in_executor(None, instagram_carousel_and_photo_engine, target_url, task_dir)
            if is_tiktok_url(target_url) and not os.listdir(task_dir):
                await loop.run_in_executor(None, tiktok_multi_engine, target_url, task_dir)

        elif target_url:
            filepath = os.path.join(task_dir, filename)
            upload_start_time = time.time()
            last_upload_update = 0

            async def upload_progress_callback(current, total):
                if cancel_event.is_set(): raise Exception("CANCELLED")
                nonlocal last_upload_update
                now = time.time()
                if now - last_upload_update > 2.0 or current == total:
                    last_upload_update = now
                    elapsed = now - upload_start_time
                    speed = current / elapsed if elapsed > 0 else 0
                    percent = (current / total * 100) if total > 0 else 0
                    bar = "█" * int(percent // 10) + "░" * (10 - int(percent // 10))
                    rem_time = (total - current) / speed if speed > 0 and total > 0 else 0
                    
                    text = (
                        f"📤 **جاري رفع الملف إلى تيليجرام...**\n[{bar}] {percent:.1f}%\n"
                        f"📦 الحجم: `{format_size(current)}` / `{format_size(total)}`\n"
                        f"⚡ السرعة: `{format_size(speed)}/s`\n"
                        f"⏱️ الوقت المنقضي: `{format_time(elapsed)}`\n"
                        f"⏳ المتبقي: `{format_time(rem_time)}`"
                    )
                    try: await status_msg.edit(text, buttons=cancel_btn)
                    except: pass

            await download_direct_async(bot, chat_id, target_url, filepath, status_msg, cancel_event, task_id)
            if cancel_event.is_set(): raise Exception("CANCELLED")

            if trim_times: filepath = trim_video_clip(filepath, trim_times[0], trim_times[1])

            video_files = split_video_file(filepath)
            for vid_file in video_files:
                duration, width, height, thumb_path = get_video_metadata_and_thumb(vid_file)
                attr = [DocumentAttributeVideo(duration=duration, w=width, h=height, supports_streaming=True)]
                await bot.send_file(chat_id, vid_file, thumb=thumb_path, attributes=attr, progress_callback=upload_progress_callback)
            
            await status_msg.delete()
            return

        # رفع نتائج منصات التواصل
        downloaded_files = [os.path.join(task_dir, f) for f in os.listdir(task_dir) if os.path.isfile(os.path.join(task_dir, f))]
        for fpath in downloaded_files:
            await bot.send_file(chat_id, fpath)
        await status_msg.delete()

    except Exception as e:
        if str(e) == "CANCELLED": await status_msg.edit("🛑 **تم إلغاء العملية.**", buttons=None)
        else: await status_msg.edit(f"❌ **خطأ:** `{str(e)}`", buttons=None)
    finally:
        ACTIVE_CANCEL_EVENTS.pop(task_id, None)
        if os.path.exists(task_dir): shutil.rmtree(task_dir, ignore_errors=True)
  
