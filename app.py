#!/usr/bin/env python3
import os
import sys
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Add local lib to path
lib_path = Path(__file__).parent / 'lib'
sys.path.insert(0, str(lib_path))

from waveshare_epd import epd2in13_V4

# Import for reading audio tags
try:
    from mutagen import File as MutagenFile
except ImportError:
    print("Error: mutagen library not found. Install with: pip install mutagen")
    sys.exit(1)

# Constants
MUSIC_DIR = "/home/jrwhite/Music/Albums/"
FONT_PATH = "./pic/Font.ttc"
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.ogg', '.wav', '.opus'}

# Display dimensions for epd2in13_V4
EPD_WIDTH = 122
EPD_HEIGHT = 250


def find_audio_files(root_dir):
    """Recursively scan for audio files."""
    audio_files = []
    for path in Path(root_dir).rglob('*'):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            audio_files.append(str(path))
    return audio_files


def get_track_info(file_path):
    """Extract title, artist, album, and cover art from audio file."""
    audio = MutagenFile(file_path)
    if audio is None:
        return None, None, None, None
    
    # Extract metadata
    title = audio.get('TIT2', ['Unknown Title'])[0] if hasattr(audio, 'get') else 'Unknown Title'
    artist = audio.get('TPE1', ['Unknown Artist'])[0] if hasattr(audio, 'get') else 'Unknown Artist'
    album = audio.get('TALB', ['Unknown Album'])[0] if hasattr(audio, 'get') else 'Unknown Album'
    
    # Handle different tag formats
    if hasattr(audio, 'tags'):
        tags = audio.tags
        if tags:
            title = str(tags.get('TIT2', tags.get('title', ['Unknown Title']))[0])
            artist = str(tags.get('TPE1', tags.get('artist', ['Unknown Artist']))[0])
            album = str(tags.get('TALB', tags.get('album', ['Unknown Album']))[0])
    
    # Extract cover art
    cover_image = None
    if hasattr(audio, 'pictures') and audio.pictures:
        # FLAC files
        cover_image = Image.open(io.BytesIO(audio.pictures[0].data))
    elif hasattr(audio, 'tags'):
        # MP3 files
        for key in audio.tags.keys():
            if 'APIC' in str(key):
                cover_image = Image.open(io.BytesIO(audio.tags[key].data))
                break
    
    return title, artist, album, cover_image


def render_now_playing(title, artist, album, cover_image):
    """Render the Now Playing screen."""
    # Create a new image in landscape mode (WIDTH x HEIGHT)
    image = Image.new('1', (EPD_WIDTH, EPD_HEIGHT), 255)  # 255 = white
    draw = ImageDraw.Draw(image)
    
    # Load font
    try:
        font_small = ImageFont.truetype(FONT_PATH, 10)
        font_medium = ImageFont.truetype(FONT_PATH, 12)
    except:
        font_small = ImageFont.load_default()
        font_medium = ImageFont.load_default()
    
    y_offset = 5
    
    # Draw cover art at top (centered)
    if cover_image:
        # Make it square and resize to fit width
        art_size = min(EPD_WIDTH - 10, 80)
        cover_image = cover_image.convert('L')  # Convert to grayscale
        cover_image.thumbnail((art_size, art_size), Image.Resampling.LANCZOS)
        
        # Convert to 1-bit (monochrome)
        cover_image = cover_image.convert('1')
        
        # Center the cover art
        x_pos = (EPD_WIDTH - cover_image.width) // 2
        image.paste(cover_image, (x_pos, y_offset))
        y_offset += cover_image.height + 10
    
    # Draw text (centered)
    # Title
    title_text = title[:30] if len(title) > 30 else title
    bbox = draw.textbbox((0, 0), title_text, font=font_medium)
    text_width = bbox[2] - bbox[0]
    x_pos = (EPD_WIDTH - text_width) // 2
    draw.text((x_pos, y_offset), title_text, font=font_medium, fill=0)
    y_offset += 15
    
    # Artist
    artist_text = artist[:30] if len(artist) > 30 else artist
    bbox = draw.textbbox((0, 0), artist_text, font=font_small)
    text_width = bbox[2] - bbox[0]
    x_pos = (EPD_WIDTH - text_width) // 2
    draw.text((x_pos, y_offset), artist_text, font=font_small, fill=0)
    y_offset += 12
    
    # Album
    album_text = album[:30] if len(album) > 30 else album
    bbox = draw.textbbox((0, 0), album_text, font=font_small)
    text_width = bbox[2] - bbox[0]
    x_pos = (EPD_WIDTH - text_width) // 2
    draw.text((x_pos, y_offset), album_text, font=font_small, fill=0)
    
    return image


def main():
    print("PiPod - Scanning for music...")
    
    # Find all audio files
    audio_files = find_audio_files(MUSIC_DIR)
    if not audio_files:
        print(f"No audio files found in {MUSIC_DIR}")
        return
    
    print(f"Found {len(audio_files)} audio files")
    
    # Pick a random track
    selected_track = random.choice(audio_files)
    print(f"Selected: {selected_track}")
    
    # Get track info
    title, artist, album, cover_image = get_track_info(selected_track)
    print(f"Title: {title}")
    print(f"Artist: {artist}")
    print(f"Album: {album}")
    print(f"Cover art: {'Yes' if cover_image else 'No'}")
    
    # Render the image
    image = render_now_playing(title, artist, album, cover_image)
    
    # Initialize and display on e-ink
    print("Initializing e-ink display...")
    epd = epd2in13_V4.EPD()
    epd.init()
    epd.Clear(0xFF)
    
    print("Displaying image...")
    epd.display(epd.getbuffer(image))
    
    print("Going to sleep...")
    epd.sleep()
    
    print("Done!")


if __name__ == '__main__':
    main()
