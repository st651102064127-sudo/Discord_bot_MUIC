import discord
from discord.ext import commands
from discord.ui import Button, View, Select
import yt_dlp
import asyncio
import lyricsgenius
import time
import datetime
from dotenv import load_dotenv

import os
load_dotenv()
# ==================== ⚙️ CONFIG (ตั้งค่าตรงนี้) ====================
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN') # เอามาจากENV
GENIUS_TOKEN = os.getenv('GENIUS_TOKEN') # เอามาจากENV 
LOFI_URL = "https://www.youtube.com/watch?v=jfKfPfyJRdk"# ลิงค์เพลงlofi
 
# ==================== 🔧 SYSTEM SETUP ====================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Setup Genius
genius = None
if GENIUS_TOKEN and GENIUS_TOKEN != 'ใส่_GENIUS_TOKEN_ที่นี่_ถ้ามี_ถ้าไม่มีปล่อยว่าง':
    genius = lyricsgenius.Genius(GENIUS_TOKEN)

# --- Global Variables ---
queue = []
current_song_info = None
current_filter = 'normal'
is_lofi_mode = False

# Variables for Time Calculation
start_time = 0
seek_position = 0 
is_seeking = False

# --- YT-DLP & FFmpeg ---
ydl_opts_search = {'format': 'bestaudio/best', 'noplaylist': True, 'quiet': True, 'default_search': 'ytsearch10', 'extract_flat': True, 'skip_download': True}
ydl_opts_playlist = {'format': 'bestaudio/best', 'extract_flat': True, 'noplaylist': False, 'playlistend': 30, 'quiet': True}
ydl_opts_play = {'format': 'bestaudio/best', 'noplaylist': True, 'quiet': True}

FFMPEG_FILTERS = {
    'bass': '-af bass=g=20',
    'nightcore': '-af asetrate=44100*1.25,aresample=44100',
    'slowed': '-af asetrate=44100*0.8,aresample=44100',
    'normal': ''
}
# !!! โค้ดที่ปรับปรุงใหม่เพื่อลดอาการกระตุก (เพิ่ม probesize และ analyzeduration) !!!
ffmpeg_base = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 10M -analyzeduration 10M'

# ==================== 🛠️ HELPER FUNCTIONS ====================

def get_current_time():
    """คำนวณเวลาปัจจุบันของเพลง (วินาที)"""
    if start_time == 0: return 0
    return max(0, time.time() - start_time)

def create_progress_bar(current_sec, total_sec, length=15):
    if total_sec == 0: return "🔴 **LIVE STREAM**"
    
    percent = current_sec / total_sec
    if percent > 1: percent = 1
    
    filled = int(length * percent)
    bar = "▬" * filled + "🔘" + "▬" * (length - filled)
    
    curr_str = str(datetime.timedelta(seconds=int(current_sec)))[2:] if current_sec < 3600 else str(datetime.timedelta(seconds=int(current_sec)))
    total_str = str(datetime.timedelta(seconds=int(total_sec)))[2:] if total_sec < 3600 else str(datetime.timedelta(seconds=int(total_sec)))
    
    return f"`{curr_str} {bar} {total_str}`"

# ==================== 🎨 GUI CLASSES ====================

class SongSelect(Select):
    """Dropdown สำหรับเลือกเพลง 10 ตัวเลือก"""
    def __init__(self, items, ctx):
        options = []
        self.items = items
        self.ctx = ctx
        for index, item in enumerate(items):
            label = item.get('title', 'Unknown')[:95]
            options.append(discord.SelectOption(label=f"{index+1}. {label}", value=str(index), emoji="🎵"))
        super().__init__(placeholder="✨ จิ้มเลือกเพลงเลย... (10 ตัวเลือก)", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        idx = int(self.values[0])
        data = self.items[idx]
        queue.append({'title': data['title'], 'url': data['url'], 'needs_process': True, 'duration': 0})
        await interaction.followup.send(f"✅ เพิ่ม **{data['title']}** ลงคิวแล้ว!", ephemeral=False)
        
        if not self.ctx.voice_client.is_playing() or is_lofi_mode:
            if is_lofi_mode and self.ctx.voice_client.is_playing(): self.ctx.voice_client.stop()
            elif not self.ctx.voice_client.is_playing(): await play_music(self.ctx)

class SearchView(View):
    """View ที่มีแค่ Dropdown"""
    def __init__(self, items, ctx):
        super().__init__(timeout=60)
        self.add_item(SongSelect(items, ctx))

class ControlView(View):
    """View หลักที่มีปุ่มควบคุมทั้งหมด"""
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx

    async def update_embed(self, interaction):
        """อัปเดต Embed เพื่อแสดงเวลาปัจจุบัน/คิวที่ถูกล้าง"""
        if not current_song_info or is_lofi_mode: return

        curr_sec = get_current_time()
        total_sec = current_song_info.get('duration', 0)
        bar_str = create_progress_bar(curr_sec, total_sec)

        embed = discord.Embed(description=f"🎶 **Now Playing:** {current_song_info['title']}", color=discord.Color.from_rgb(255, 105, 180))
        if current_song_info.get('thumbnail'): embed.set_thumbnail(url=current_song_info['thumbnail'])
        embed.add_field(name="⏳ Timeline", value=bar_str, inline=False)
        
        mode_text = current_filter.capitalize() if current_filter else "Normal"
        embed.set_footer(text=f"🎚️ Mode: {mode_text} | ใช้ปุ่ม Full Queue เพื่อดูรายการถัดไป")

        try: await interaction.message.edit(embed=embed, view=self)
        except: pass

    # --- Row 0: Basic Controls ---
    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.primary, row=0)
    async def play_pause(self, interaction: discord.Interaction, button: Button):
        vc = interaction.guild.voice_client
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ พักแป๊บนึง!", ephemeral=True)
        else:
            vc.resume()
            await interaction.response.send_message("▶️ ลุยต่อโลด!", ephemeral=True)
        await self.update_embed(interaction)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.danger, row=0)
    async def skip(self, interaction: discord.Interaction, button: Button):
        vc = interaction.guild.voice_client
        if vc:
            vc.stop()
            await interaction.response.send_message("⏩ Skip!", ephemeral=True)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        await self.update_embed(interaction)

    # --- Row 1: Seeking ---
    @discord.ui.button(label="⏪ -10s", style=discord.ButtonStyle.secondary, row=1)
    async def rew_10(self, interaction: discord.Interaction, button: Button):
        await self.do_seek(interaction, -10)

    @discord.ui.button(label="⏩ +30s", style=discord.ButtonStyle.secondary, row=1)
    async def fwd_30(self, interaction: discord.Interaction, button: Button):
        await self.do_seek(interaction, 30)

    async def do_seek(self, interaction, sec_change):
        global seek_position, is_seeking
        if not current_song_info or is_lofi_mode: return await interaction.response.send_message("❌ Seek ไม่ได้จ้า", ephemeral=True)

        current = get_current_time()
        new_pos = max(0, current + sec_change)
        
        await interaction.response.send_message(f"⏱️ วาร์ปไปวินาทีที่ {int(new_pos)}...", ephemeral=True)
        
        is_seeking = True 
        seek_position = new_pos 
        
        if interaction.guild.voice_client: interaction.guild.voice_client.stop()

    # --- Row 2: Filters (Playing continuously) ---
    async def apply_filter(self, interaction, filter_name):
        global current_filter, is_seeking, seek_position
        
        current_time = get_current_time() 
        
        current_filter = filter_name
        seek_position = current_time 
        is_seeking = True 
        
        await interaction.response.send_message(f"🎚️ ปรับโหมด {filter_name} (เล่นต่อที่ {int(current_time)}s)", ephemeral=True)
        
        if interaction.guild.voice_client: interaction.guild.voice_client.stop()

    @discord.ui.button(label="🔊 Bass", style=discord.ButtonStyle.success, row=2)
    async def btn_bass(self, interaction: discord.Interaction, button: Button): await self.apply_filter(interaction, 'bass')

    @discord.ui.button(label="🐿️ Night", style=discord.ButtonStyle.success, row=2)
    async def btn_nc(self, interaction: discord.Interaction, button: Button): await self.apply_filter(interaction, 'nightcore')
        
    @discord.ui.button(label="🐢 Slow", style=discord.ButtonStyle.success, row=2)
    async def btn_slow(self, interaction: discord.Interaction, button: Button): await self.apply_filter(interaction, 'slowed')

    @discord.ui.button(label="🚫 Normal", style=discord.ButtonStyle.secondary, row=2)
    async def btn_norm(self, interaction: discord.Interaction, button: Button): await self.apply_filter(interaction, 'normal')

    # --- Row 3: Utilities (Queue & Lyrics) ---
    @discord.ui.button(label="📜 Lyrics", style=discord.ButtonStyle.gray, row=3)
    async def lyrics(self, interaction: discord.Interaction, button: Button):
        if not current_song_info: return
        await interaction.response.defer(ephemeral=True)
        title = current_song_info['title'].split('(')[0].split('[')[0]
        if genius:
            try:
                s = genius.search_song(title)
                if s: await interaction.followup.send(f"📜 **{s.title}**\n\n{s.lyrics[:1900]}...", ephemeral=True)
                else: await interaction.followup.send("🥺 หาเนื้อเพลงไม่เจอ", ephemeral=True)
            except: await interaction.followup.send("❌ Error genius", ephemeral=True)
        else: await interaction.followup.send("⚠️ ใส่ Token Genius ในโค้ดก่อนนะ", ephemeral=True)

    @discord.ui.button(label="🗑️ Clear Queue", style=discord.ButtonStyle.danger, row=3)
    async def clear_queue_button(self, interaction: discord.Interaction, button: Button):
        global queue
        queue = []
        await interaction.response.send_message("🗑️ **ล้างคิวเพลงทั้งหมดเรียบร้อยแล้วจ้า!**", ephemeral=True)
        await self.update_embed(interaction)

    @discord.ui.button(label="🗂️ Full Queue", style=discord.ButtonStyle.primary, row=3)
    async def full_queue_list(self, interaction: discord.Interaction, button: Button):
        if not queue:
            return await interaction.response.send_message("📭 คิวว่างเปล่าจ้า", ephemeral=True)
        
        list_str = [f"`{i+1}.` {song['title'][:60]}" for i, song in enumerate(queue)]
        
        full_message = "🎼 **รายการเพลงในคิวทั้งหมด:**\n" + "\n".join(list_str)
        
        await interaction.response.send_message(full_message[:1990], ephemeral=True)
        
# ==================== 🧠 LOGIC (การจัดการการเล่นเพลง) ====================

async def play_music(ctx, start_at_sec=0):
    global current_song_info, is_lofi_mode, start_time, seek_position, is_seeking
    
    vc = ctx.voice_client
    if not vc: return

    # 1. จัดการคิวเพลง/Seek/Filter
    if is_seeking and current_song_info:
        song = current_song_info
        start_at_sec = seek_position
        is_seeking = False
    elif len(queue) > 0:
        is_lofi_mode = False
        song = queue.pop(0)
        seek_position = 0
        start_at_sec = 0
    else:
        await play_lofi(ctx)
        return

    # 2. Process URL
    final_url = song['url']
    final_title = song['title']
    duration = song.get('duration', 0)
    thumbnail = song.get('thumbnail', '')

    if song.get('needs_process') or start_at_sec > 0:
         with yt_dlp.YoutubeDL(ydl_opts_play) as ydl:
            try:
                info = ydl.extract_info(song['url'] if 'url' in song else final_url, download=False)
                final_url = info['url']
                final_title = info['title']
                duration = info.get('duration', 0)
                thumbnail = info.get('thumbnail', '')
                
                song.update({'url': song['url'], 'title': final_title, 'duration': duration, 'thumbnail': thumbnail, 'needs_process': False})
            except Exception as e:
                print(f"Error: {e}")
                await ctx.send(f"❌ เพลง **{final_title}** มีปัญหา ข้ามนะเตง")
                await play_music(ctx)
                return

    current_song_info = song
    
    # 3. เตรียม FFmpeg Options สำหรับ Seek และ Filter (ใช้ค่า ffmpeg_base ที่ปรับปรุงแล้ว)
    filter_str = FFMPEG_FILTERS.get(current_filter, '')
    ffmpeg_opts = {'before_options': f'{ffmpeg_base} -ss {start_at_sec}', 'options': f'-vn {filter_str}'}

    source = discord.FFmpegPCMAudio(final_url, **ffmpeg_opts)
    
    # 4. เริ่มนับเวลา 
    start_time = time.time() - start_at_sec 

    def after_playing(error):
        fut = asyncio.run_coroutine_threadsafe(play_music(ctx), bot.loop)
        try: fut.result()
        except: pass

    vc.play(source, after=after_playing)
    
    # 5. ส่ง Embed
    bar_str = create_progress_bar(start_at_sec, duration)
    embed = discord.Embed(description=f"🎶 **Now Playing:** {final_title}", color=discord.Color.from_rgb(255, 105, 180))
    if thumbnail: embed.set_thumbnail(url=thumbnail)
    embed.add_field(name="⏳ Timeline", value=bar_str, inline=False)
    embed.set_footer(text=f"🎚️ Mode: {current_filter.capitalize()} | ใช้ปุ่ม Full Queue เพื่อดูรายการถัดไป")
    
    await ctx.send(embed=embed, view=ControlView(ctx))

async def play_lofi(ctx):
    """เปิด Lo-Fi เมื่อคิวว่าง"""
    global current_song_info, is_lofi_mode
    current_song_info = None
    is_lofi_mode = True
    vc = ctx.voice_client
    
    real_url = LOFI_URL
    with yt_dlp.YoutubeDL(ydl_opts_play) as ydl:
        try: real_url = ydl.extract_info(LOFI_URL, download=False)['url']
        except: pass
    
    source = discord.FFmpegPCMAudio(real_url, **{'before_options': ffmpeg_base, 'options': '-vn'})
    def after_lofi(e):
        if is_lofi_mode: asyncio.run_coroutine_threadsafe(play_music(ctx), bot.loop)
    
    vc.play(source, after=after_lofi)
    embed = discord.Embed(title="☕ 24/7 Lo-Fi Radio", description="*พักผ่อนชิลๆ ระหว่างรอเพลงต่อไป...*", color=discord.Color.orange())
    embed.set_image(url="https://media.giphy.com/media/5wWf7H0qoWaNnkFHRh5/giphy.gif")
    await ctx.send(embed=embed)

# ==================== 📨 COMMANDS & EVENTS ====================
@bot.event
async def on_ready(): print(f'💖 Bot is Online: {bot.user}')

@bot.event
async def on_message(message):
    if message.author.bot: return
    if message.channel.name == DEDICATED_CHANNEL_NAME:
        await message.delete()
        ctx = await bot.get_context(message)
        if not ctx.author.voice: return await ctx.send(f"เข้าห้องเสียงก่อนจ้า", delete_after=5)
        if not ctx.voice_client: await ctx.author.voice.channel.connect()

        query = message.content
        if "http" in query and "list=" in query:
             await ctx.send("📀 Loading Playlist...", delete_after=5)
             loop = asyncio.get_event_loop()
             data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts_playlist).extract_info(query, download=False))
             if 'entries' in data:
                 for e in data['entries']: 
                    if e: queue.append({'title': e.get('title','Unknown'), 'url': e['url'], 'duration': e.get('duration',0), 'needs_process': True})
                 if not ctx.voice_client.is_playing() or is_lofi_mode: 
                     if is_lofi_mode: ctx.voice_client.stop()
                     else: await play_music(ctx)
        
        elif query.startswith("http"):
            await ctx.send("⚡ Fast Load...", delete_after=5)
            with yt_dlp.YoutubeDL(ydl_opts_play) as ydl:
                try:
                    info = ydl.extract_info(query, download=False)
                    queue.append({'title': info['title'], 'url': info['url'], 'duration': info.get('duration',0), 'needs_process': False})
                    if not ctx.voice_client.is_playing() or is_lofi_mode:
                        if is_lofi_mode: ctx.voice_client.stop()
                        else: await play_music(ctx)
                except:
                    await ctx.send("❌ ลิงก์เสียอะเตง", delete_after=5)
        else:
            await ctx.send(f"🔍 กำลังหา: **{query}**...", delete_after=10)
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts_search).extract_info(f"ytsearch10:{query}", download=False))
            if 'entries' in data: 
                await ctx.send("⬇️ เลือกเลย:", view=SearchView(data['entries'], ctx))

    await bot.process_commands(message)

@bot.command()
async def play(ctx): pass 
@bot.command()
async def leave(ctx): 
    if ctx.voice_client: await ctx.voice_client.disconnect()

bot.run(DISCORD_TOKEN)