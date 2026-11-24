"""Writes book_spec.json: the list of transitions in the TRANSITIONS BOOK (ids, kind, category, licence, use, creator match).
Batch 1 = ids 1-20 (best gl-transitions + our Meta overlays). Run: python make_spec.py"""
import json
from pathlib import Path

GLV = "3.3.0"   # gl-transitions npm version the runner downloads (pinned)
GL_LIC = "MIT (gl-transitions, https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE ; licence line in each shader header)"
LOT_LIC = "Lottie Simple License FL 9.13.21 (https://lottiefiles.com/page/license) - commercial OK, no attribution required"
KIT_LIC = "own (Meta AI generated overlay, R2 s2work/shorts1/clips/kit_meta/)"
FF_LIC = "ffmpeg xfade filter (LGPL code, output is ours)"

M = {
 "whip": "Nitish Flydubai 00:10 whip; Dhruv Always Tired 00:01 whip-blur map zoom; Dhruv Epstein 00:28.5 quick whip cut",
 "flash": "Nitish Flydubai 00:14/00:15 white flash; Dhruv Japan 27:13.5 white flash into presenter",
 "leak": "Dhruv Japan 00:13.6 orange light-leak flash; Dhruv Epstein 00:15.5 light-leak; Dhruv Bhangarh 00:20.8 yellow leak flash",
 "burn": "Dhruv Japan 00:35.4 yellow film-burn wipe; Dhruv Always Tired 18:57 film-burn wipe (outro)",
 "glitch": "Nitish Flydubai 00:04/00:15 glitch cuts; Dhruv Japan 00:57.1; Dhruv Always Tired 01:44.8 glitch jump cut",
 "dissolve": "Dhruv Epstein 00:12.5 cross-dissolve; Dhruv Japan 01:13.7 bloom dissolve; Dhruv Bhangarh 03:13.7 light dissolve",
 "dip": "Dhruv Always Tired 02:51.2 / Bhangarh 11:05.4 / Japan 07:37.4 dip to black",
 "zoom": "Dhruv Bhangarh 03:56.5 punch-in; map zooms Dhruv Kailash 15:53.5, Nitish Daring Operation 01:17",
 "split": "Nitish Flydubai 00:39 / 03:12 diagonal split wipe",
 "iris": "Nitish Flydubai 03:01 hex/iris graphic wipe",
 "paper": "Dhruv Epstein 00:04 wipe across a document; Dhruv Japan 00:17.4 fast slide on a timeline",
 "smoke": "Dhruv Kailash 06:03.8 push through smoke into whip flash",
 "": "",
}

# (name, kind, category, frames, use, match-key, extra)
E = [
 # ---------------- BATCH 1 (ids 1-20) ----------------
 ("WhipPan", "gl", "energetic", 15, ["place change", "action"], "whip", {}),
 ("CrossZoom", "gl", "energetic", 20, ["action", "reveal"], "zoom", {}),
 ("FilmBurn", "gl", "light/film", 30, ["time jump", "chapter"], "burn", {}),
 ("LensFlare", "gl", "light/film", 24, ["title", "reveal"], "leak", {}),
 ("LightWipe", "gl", "light/film", 24, ["place change", "title"], "leak", {}),
 ("GlitchMemories", "gl", "glitch/tech", 18, ["action", "reveal"], "glitch", {}),
 ("StripDatamoshGlitch", "gl", "glitch/tech", 18, ["action", "reveal"], "glitch", {}),
 ("InkBleed", "gl", "shape/liquid", 36, ["time jump", "chapter"], "dissolve", {}),
 ("SmokeDissolve", "gl", "shape/liquid", 36, ["time jump", "reveal"], "smoke", {}),
 ("BurnThrough", "gl", "dramatic", 30, ["reveal", "chapter"], "burn", {}),
 ("Cube", "gl", "3D", 30, ["place change", "chapter"], "", {}),
 ("InvertedPageCurl", "gl", "3D", 36, ["time jump", "chapter"], "paper", {"licence": "BSD 3-Clause (gl-transitions InvertedPageCurl header)"}),
 ("WarpSpeed", "gl", "dramatic", 30, ["place change", "title"], "zoom", {}),
 ("Overexposure", "gl", "light/film", 18, ["reveal", "action"], "flash", {}),
 ("light_leak", "kit", "light/film", 40, ["time jump", "title"], "leak", {"mode": "screen"}),
 ("film_burn", "kit", "light/film", 40, ["time jump", "chapter"], "burn", {"mode": "screen"}),
 ("flash_white", "kit", "light/film", 30, ["action", "reveal"], "flash", {"mode": "screen"}),
 ("glitch_vhs", "kit", "glitch/tech", 24, ["action", "reveal"], "glitch", {"mode": "screen"}),
 ("ink_bleed", "kit", "shape/liquid", 40, ["time jump", "chapter"], "dissolve", {"mode": "matte_inv"}),
 ("smoke_wipe", "kit", "shape/liquid", 40, ["reveal", "place change"], "smoke", {"mode": "matte"}),
 # ---------------- BATCH 2 ----------------
 ("FilmDissolve", "gl", "subtle/clean", 24, ["time jump"], "dissolve", {}),
 ("DefocusBlur", "gl", "subtle/clean", 24, ["time jump", "reveal"], "dissolve", {}),
 ("FadeGrayscale", "gl", "subtle/clean", 24, ["time jump"], "dissolve", {}),
 ("BokehDissolve", "gl", "subtle/clean", 30, ["time jump", "title"], "dissolve", {}),
 ("DirectionalWipe", "gl", "subtle/clean", 20, ["place change"], "split", {}),
 ("SimpleZoomOut", "gl", "subtle/clean", 20, ["reveal"], "zoom", {}),
 ("WhipSpin", "gl", "energetic", 15, ["action"], "whip", {}),
 ("WhipTilt", "gl", "energetic", 15, ["place change", "action"], "whip", {}),
 ("CrossWarp", "gl", "energetic", 20, ["place change"], "zoom", {}),
 ("DirectionalEased", "gl", "energetic", 15, ["place change"], "whip", {}),
 ("StretchSlide", "gl", "energetic", 18, ["action"], "whip", {}),
 ("CameraShake", "gl", "energetic", 15, ["action"], "flash", {}),
 ("ZoomInOut", "gl", "energetic", 20, ["place change", "reveal"], "zoom", {}),
 ("Revolve", "gl", "energetic", 20, ["action"], "whip", {}),
 ("SplitSlideInOutHorizontal", "gl", "energetic", 18, ["place change"], "split", {}),
 ("LightStreaks", "gl", "light/film", 24, ["title", "reveal"], "leak", {}),
 ("LightRays", "gl", "light/film", 24, ["title", "chapter"], "leak", {}),
 ("BloomDissolve", "gl", "light/film", 24, ["time jump", "title"], "dissolve", {}),
 ("FlashFade", "gl", "light/film", 15, ["action", "reveal"], "flash", {}),
 ("DreamyZoom", "gl", "light/film", 24, ["time jump", "title"], "flash", {}),
 ("LightningFlash", "gl", "light/film", 20, ["action", "reveal"], "flash", {}),
 ("GlitchDisplace", "gl", "glitch/tech", 18, ["action"], "glitch", {}),
 ("GlitchFlicker", "gl", "glitch/tech", 18, ["action", "reveal"], "glitch", {}),
 ("VhsTracking", "gl", "glitch/tech", 24, ["time jump"], "glitch", {}),
 ("TvLostSignal", "gl", "glitch/tech", 24, ["time jump", "reveal"], "glitch", {}),
 ("StaticFade", "gl", "glitch/tech", 15, ["action"], "glitch", {}),
 ("CodeRain", "gl", "glitch/tech", 30, ["reveal"], "glitch", {}),
 ("TvTurnOff", "gl", "glitch/tech", 24, ["chapter"], "dip", {}),
 ("GravityLens", "gl", "dramatic", 30, ["reveal"], "", {}),
 ("Shatter", "gl", "dramatic", 30, ["action", "reveal"], "", {}),
 ("Vaporize", "gl", "dramatic", 36, ["reveal", "time jump"], "smoke", {}),
 ("Disintegrate", "gl", "dramatic", 36, ["reveal"], "smoke", {}),
 ("SpotlightWipe", "gl", "dramatic", 36, ["reveal", "title"], "iris", {}),
 ("PhotoDevelop", "gl", "dramatic", 30, ["time jump"], "dissolve", {}),
 ("GooeyWipe", "gl", "shape/liquid", 30, ["place change"], "", {}),
 ("LiquidFill", "gl", "shape/liquid", 30, ["place change", "time jump"], "", {}),
 ("BrushWipe", "gl", "shape/liquid", 30, ["chapter", "title"], "paper", {}),
 ("SplatterWipe", "gl", "shape/liquid", 30, ["action", "title"], "", {}),
 ("PaperTear", "gl", "shape/liquid", 30, ["reveal", "chapter"], "paper", {}),
 ("FlameWipe", "gl", "shape/liquid", 30, ["action", "reveal"], "burn", {}),
 ("WaterDrop", "gl", "shape/liquid", 30, ["time jump"], "dissolve", {}),
 ("ShutterWipe", "gl", "shape/liquid", 20, ["place change", "action"], "split", {}),
 ("InsideCube", "gl", "3D", 30, ["place change"], "", {}),
 ("BookFlip", "gl", "3D", 36, ["chapter", "time jump"], "paper", {}),
 ("Doorway", "gl", "3D", 30, ["reveal", "place change"], "", {}),
 ("Prisms", "gl", "3D", 30, ["chapter"], "split", {}),
 ("embers", "kit", "light/film", 40, ["action", "chapter"], "burn", {"mode": "screen"}),
 ("dust_beam", "kit", "light/film", 40, ["time jump", "title"], "leak", {"mode": "screen"}),
 ("red_alert", "kit", "dramatic", 30, ["reveal", "action"], "", {"mode": "screen"}),
 ("paper_tear", "kit", "shape/liquid", 40, ["time jump", "chapter"], "paper", {"mode": "matte_inv", "thr": [0.85, 0.97]}),
 ("fog_through", "kit", "dramatic", 40, ["place change", "reveal"], "smoke", {"mode": "dip"}),
 ("fade", "xfade", "subtle/clean", 20, ["time jump"], "dissolve", {}),
 ("fadeblack", "xfade", "dramatic", 24, ["reveal", "chapter"], "dip", {}),
 ("fadewhite", "xfade", "light/film", 10, ["action", "reveal"], "flash", {}),
 ("smoothleft", "xfade", "energetic", 12, ["place change"], "whip", {}),
 ("zoomin", "xfade", "energetic", 15, ["reveal"], "zoom", {}),
 ("circleopen", "xfade", "shape/liquid", 20, ["reveal"], "iris", {}),
 ("hblur", "xfade", "subtle/clean", 15, ["time jump"], "dissolve", {}),
 ("diagtl", "xfade", "subtle/clean", 18, ["place change"], "split", {}),
]

LOT = [  # LottieFiles free animations (page says "Lottie Simple License"); asset fetched at render time, never stored
 ("circle-transition-d4XT1YXqsi", "https://assets-v2.lottiefiles.com/a/34c3d486-fd9b-11ee-b21f-3b248d04803b/Fj5o4ECt3C.lottie"),
 ("liquid-transition-screen-ngZeee3Tzl", "https://assets-v2.lottiefiles.com/a/26c61cf2-1167-11ee-8bff-cb4e2cb5f1f1/d6zCNDTi0u.lottie"),
 ("transition-01-bKunH9juUm", "https://assets-v2.lottiefiles.com/a/4ccecca8-1173-11ee-9c4b-2313e743b1f9/pUXIgBh44q.lottie"),
 ("transition-a8zrS06lTH", "https://assets-v2.lottiefiles.com/a/58763396-1152-11ee-bb5d-7f7dcc1fcad2/v73H3Up1Lc.lottie"),
 ("menu-page-transition-bNxcsVj25R", "https://assets-v2.lottiefiles.com/a/42cb212e-1151-11ee-bff9-77827c2c1f9c/PGbMe98oUl.lottie"),
 ("circle-radial-wipe-sn5GSvaUiQ", "https://assets-v2.lottiefiles.com/a/318b8628-1152-11ee-beda-6f9e8f339229/Y59LvRTtM1.lottie"),
 ("simple-screen-wipe-dcDpZyZXPC", "https://assets-v2.lottiefiles.com/a/0447a4ee-1161-11ee-b6e3-7f0642c06a62/6M3XV5CQWk.lottie"),
 ("stripe-wipe-jXJi0tLSPm", "https://assets-v2.lottiefiles.com/a/4371daaa-1151-11ee-81e9-87d8c424b780/L4dWoBXXaS.lottie"),
 ("wavy-wipe-yfUsRZfqsd", "https://assets-v2.lottiefiles.com/a/437004fa-1151-11ee-81e8-0fce47bd0a08/OknhJZftWa.lottie"),
 ("geometric-shapes-transition-zxlHGObXuz", "https://assets-v2.lottiefiles.com/a/80e7b8f4-1161-11ee-940f-0f9ef4c83192/jSs2QwZ0Ap.lottie"),
 ("mask-reveal-transition-zxVeEUYgxF", "https://assets-v2.lottiefiles.com/a/56d3047c-116d-11ee-ae13-cb033a798628/AXQ4aBEa9L.lottie"),
 ("transition-one-oFFQiGDOIu", "https://assets-v2.lottiefiles.com/a/9988c1c7-d8ad-496e-969f-0bded272e652/oFFQiGDOIu.lottie"),
 ("wiper-transition-V5NU5imdlR", "https://assets-v2.lottiefiles.com/a/91a6ac64-1188-11ee-b8f5-7342a402d9c7/v8cjXzBOIH.lottie"),
 ("gdf-wipe-animation-2alDHGZcT9", "https://assets-v2.lottiefiles.com/a/4884eaf2-9502-11ee-8d36-cff82efb8a83/uxfB9NLgf5.lottie"),
]

SRC = {"gl": "gl-transitions@" + GLV, "kit": "our Meta overlay (kit_meta)", "xfade": "ffmpeg xfade", "lottie": "LottieFiles"}
LIC = {"gl": GL_LIC, "kit": KIT_LIC, "xfade": FF_LIC, "lottie": LOT_LIC}

out = []
for i, (name, kind, cat, fr, use, mk, extra) in enumerate(E, 1):
    d = {"id": i, "name": name, "kind": kind, "category": cat, "source": SRC[kind], "licence": extra.pop("licence", LIC[kind]),
         "frames": fr, "use": use, "creator_match": M[mk], "batch": 1 if i <= 20 else 2}
    d.update(extra); out.append(d)
for slug, url in LOT:
    i = len(out) + 1
    out.append({"id": i, "name": "Lottie_" + slug.rsplit("-", 1)[0].replace("-", "_"), "kind": "lottie", "category": "shape/liquid",
                "source": "LottieFiles https://lottiefiles.com/free-animation/" + slug, "licence": LOT_LIC, "frames": 30,
                "use": ["chapter", "title", "place change"], "creator_match": M["iris"] if "circle" in slug else "", "batch": 2, "url": url})

spec = {"gl_version": GLV, "size": [640, 360], "fps": 30, "seconds": 3,
        "clip_a": "s2work/shorts1/clips/ic814/ic814_kathmandu_valley_1.mp4", "clip_b": "s2work/shorts1/clips/ic814/ic814_plane_kdh_aerial_1.mp4",
        "kit_prefix": "s2work/shorts1/clips/kit_meta/kit_", "out_prefix": "s2work/cinekit/transitions_book/", "items": out}
Path(__file__).with_name("book_spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
print(len(out), "items;", sum(1 for x in out if x["batch"] == 1), "in batch 1")
