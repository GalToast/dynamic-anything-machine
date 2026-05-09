#!/usr/bin/env python3
"""
Generate demo assets for DynamicAnythingMachine:
- OG Image (1200x630)
- Demo screenshots (workflow, output, landing)
"""

from PIL import Image, ImageDraw, ImageFont
import os

# Color palette from the dark theme
BG_DEEP = (13, 15, 18)          # #0d0f12
BG_PRIMARY = (17, 19, 24)       # #111318
BG_SECONDARY = (22, 26, 33)     # #161a21
BORDER_SUBTLE = (42, 48, 64)    # #2a3040
TEXT_MUTED = (90, 100, 120)     # #5a6478
TEXT_SECONDARY = (136, 146, 164) # #8892a4
TEXT_PRIMARY = (200, 208, 224)  # #c8d0e0
TEXT_BRIGHT = (232, 236, 244)    # #e8ecf4
ACCENT_CYAN = (0, 212, 255)     # #00d4ff
ACCENT_ORANGE = (255, 107, 53)  # #ff6b35
STATUS_MANAGER = (255, 184, 48)  # #ffb830
STATUS_WORKER = (0, 212, 255)   # #00d4ff
STATUS_SYNTHESIS = (181, 123, 255) # #b57bff
STATUS_ACTIVE = (0, 255, 136)    # #00ff88

def load_font(size, bold=False):
    """Try to load a system font, fallback to default."""
    try:
        if bold:
            return ImageFont.truetype("C:\\Windows\\Fonts\\seguiemj.ttf", size)
        return ImageFont.truetype("C:\\Windows\\Fonts\\arial.ttf", size)
    except:
        return ImageFont.load_default()

def create_gradient_background(width, height, color1, color2, direction="vertical"):
    """Create a gradient background."""
    img = Image.new("RGB", (width, height), color1)
    draw = ImageDraw.Draw(img)
    for i in range(height if direction == "vertical" else width):
        ratio = i / (height if direction == "vertical" else width)
        r = int(color1[0] + (color2[0] - color1[0]) * ratio)
        g = int(color1[1] + (color2[1] - color1[1]) * ratio)
        b = int(color1[2] + (color2[2] - color1[2]) * ratio)
        if direction == "vertical":
            draw.rectangle([(0, i), (width, i+1)], fill=(r, g, b))
        else:
            draw.rectangle([(i, 0), (i+1, height)], fill=(r, g, b))
    return img

def create_og_image():
    """Create 1200x630 OG image for LinkedIn/Twitter sharing."""
    width, height = 1200, 630
    img = create_gradient_background(width, height, BG_DEEP, BG_PRIMARY)

    draw = ImageDraw.Draw(img)

    # Load fonts
    try:
        font_title = ImageFont.truetype("C:\\Windows\\Fonts\\SegUIVar.ttf", 72)
        font_subtitle = ImageFont.truetype("C:\\Windows\\Fonts\\SegUIVar.ttf", 32)
        font_stats = ImageFont.truetype("C:\\Windows\\Fonts\\seguisb.ttf", 24)
        font_tagline = ImageFont.truetype("C:\\Windows\\Fonts\\SegUIVar.ttf", 22)
    except:
        font_title = ImageFont.load_default()
        font_subtitle = font_title
        font_stats = font_title
        font_tagline = font_title

    # Draw decorative elements
    # Grid pattern
    for x in range(0, width, 40):
        draw.line([(x, 0), (x, height)], fill=(0, 212, 255, 15), width=1)
    for y in range(0, height, 40):
        draw.line([(0, y), (width, y)], fill=(0, 212, 255, 15), width=1)

    # Glowing orb effect
    for r in range(200, 0, -2):
        alpha = int(15 * (1 - r/200))
        draw.ellipse([(width//2 - r, height//3 - r), (width//2 + r, height//3 + r)],
                    fill=(0, 212, 255, alpha))

    # Main title
    draw.text((width//2, 180), "DynamicAnythingMachine",
              font=font_title, fill=TEXT_BRIGHT, anchor="mm")

    # Subtitle
    draw.text((width//2, 250), "Multi-Agent Orchestration Engine",
              font=font_subtitle, fill=ACCENT_CYAN, anchor="mm")

    # Stats line
    stats_text = "28 nodes  ·  6 seams  ·  18 workers  ·  Real-time execution"
    draw.text((width//2, 310), stats_text,
              font=font_stats, fill=TEXT_SECONDARY, anchor="mm")

    # Tagline
    draw.text((width//2, 400), '"Tell it to do anything. It plans, coordinates, and executes."',
              font=font_tagline, fill=TEXT_MUTED, anchor="mm")

    # Bottom color bar
    draw.rectangle([(0, height-8), (width, height)], fill=ACCENT_CYAN)
    draw.rectangle([(0, height-8), (width//4, height)], fill=ACCENT_ORANGE)
    draw.rectangle([(width//2, height-8), (width*3//4, height)], fill=STATUS_SYNTHESIS)

    # Corner nodes visualization
    # Phase 1 indicator
    draw.rounded_rectangle([(50, 480), (200, 560)], radius=8,
                           fill=BG_SECONDARY, outline=ACCENT_CYAN, width=1)
    draw.text((125, 500), "PHASE 1", font=font_stats, fill=ACCENT_CYAN, anchor="mm")
    draw.text((125, 530), "Isolated Analysis", font=ImageFont.load_default(), fill=TEXT_SECONDARY, anchor="mm")

    # Arrow
    draw.text((250, 520), ">>>", font=font_stats, fill=ACCENT_ORANGE, anchor="mm")

    # Phase 2 indicator
    draw.rounded_rectangle([(300, 480), (500, 560)], radius=8,
                           fill=BG_SECONDARY, outline=STATUS_SYNTHESIS, width=1)
    draw.text((400, 500), "PHASE 2", font=font_stats, fill=STATUS_SYNTHESIS, anchor="mm")
    draw.text((400, 530), "Pre-built Graph", font=ImageFont.load_default(), fill=TEXT_SECONDARY, anchor="mm")

    # Node counts
    draw.rounded_rectangle([(550, 480), (750, 560)], radius=8,
                           fill=BG_SECONDARY, outline=STATUS_MANAGER, width=1)
    draw.text((650, 500), "6 Managers", font=font_stats, fill=STATUS_MANAGER, anchor="mm")
    draw.text((650, 530), "18 Workers", font=font_stats, fill=ACCENT_CYAN, anchor="mm")

    # Output indicator
    draw.rounded_rectangle([(800, 480), (1000, 560)], radius=8,
                           fill=BG_SECONDARY, outline=STATUS_ACTIVE, width=1)
    draw.text((900, 500), "SYNTHESIS", font=font_stats, fill=STATUS_SYNTHESIS, anchor="mm")
    draw.text((900, 530), "6,357 chars output", font=ImageFont.load_default(), fill=TEXT_SECONDARY, anchor="mm")

    # Save
    output_path = r"C:\Users\HP\Desktop\Temp while my comp is at the shop\dynamic-graph-compiler\web-demo\static\img\og-image.png"
    img.save(output_path, "PNG", quality=95)
    print(f"Created OG image: {output_path}")
    return output_path

def create_demo_workflow():
    """Create demo-workflow.png showing execution progress."""
    width, height = 1280, 800
    img = create_gradient_background(width, height, BG_DEEP, BG_PRIMARY)
    draw = ImageDraw.Draw(img)

    # Header
    draw.rectangle([(0, 0), (width, 60)], fill=BG_SECONDARY)
    draw.text((40, 30), "DynamicAnythingMachine — Live Execution",
              font=ImageFont.load_default(), fill=TEXT_BRIGHT, anchor="lm")

    # Status badge
    draw.rounded_rectangle([(width-150, 15), (width-50, 45)], radius=4,
                           fill=(0, 255, 136, 50), outline=STATUS_ACTIVE, width=1)
    draw.text((width-100, 30), "LIVE", font=ImageFont.load_default(), fill=STATUS_ACTIVE, anchor="mm")

    # Goal display
    draw.rounded_rectangle([(40, 80), (width-40, 140)], radius=8, fill=BG_SECONDARY)
    draw.text((60, 95), "GOAL:", font=ImageFont.load_default(), fill=TEXT_MUTED)
    draw.text((60, 120), '"Research LLM fine-tuning"',
              font=ImageFont.load_default(), fill=TEXT_BRIGHT)

    # Phase 1 progress
    draw.rounded_rectangle([(40, 160), (400, 280)], radius=8, fill=BG_SECONDARY)
    draw.text((60, 180), "PHASE 1: Analysis", font=ImageFont.load_default(), fill=ACCENT_CYAN)
    draw.rectangle([(60, 200), (360, 215)], fill=BORDER_SUBTLE)  # progress bar bg
    draw.rectangle([(60, 200), (300, 215)], fill=STATUS_ACTIVE)   # progress bar fill
    draw.text((60, 225), "Seam plan generated", font=ImageFont.load_default(), fill=TEXT_SECONDARY)
    draw.text((60, 250), "6 seams identified", font=ImageFont.load_default(), fill=STATUS_MANAGER)

    # Phase 2 progress
    draw.rounded_rectangle([(420, 160), (780, 280)], radius=8, fill=BG_SECONDARY)
    draw.text((440, 180), "PHASE 2: Execution", font=ImageFont.load_default(), fill=STATUS_SYNTHESIS)
    draw.rectangle([(440, 200), (740, 215)], fill=BORDER_SUBTLE)
    draw.rectangle([(440, 200), (550, 215)], fill=ACCENT_ORANGE)
    draw.text((440, 225), "4/6 managers active", font=ImageFont.load_default(), fill=TEXT_SECONDARY)
    draw.text((440, 250), "12/18 workers running", font=ImageFont.load_default(), fill=ACCENT_CYAN)

    # Iteration counter
    draw.rounded_rectangle([(800, 160), (1000, 280)], radius=8, fill=BG_SECONDARY)
    draw.text((900, 190), "Iteration", font=ImageFont.load_default(), fill=TEXT_MUTED)
    draw.text((900, 240), "5/7", font=ImageFont.load_default(), fill=TEXT_BRIGHT, anchor="mm")

    # Graph visualization area
    draw.rounded_rectangle([(40, 300), (width-40, 550)], radius=8, fill=BG_SECONDARY)
    draw.text((60, 320), "Graph Visualization", font=ImageFont.load_default(), fill=TEXT_SECONDARY)

    # Simplified node visualization
    # Router node
    draw.ellipse([(600, 340), (680, 400)], fill=ACCENT_CYAN)
    draw.text((640, 370), "R", font=ImageFont.load_default(), fill=BG_DEEP, anchor="mm")

    # Orchestrator
    draw.ellipse([(580, 420), (700, 480)], fill=ACCENT_ORANGE)
    draw.text((640, 450), "O", font=ImageFont.load_default(), fill=BG_DEEP, anchor="mm")

    # Managers (active = brighter)
    manager_positions = [(480, 340), (560, 340), (640, 340), (720, 340), (800, 340), (880, 340)]
    for i, (x, y) in enumerate(manager_positions[:4]):  # First 4 active
        draw.ellipse([(x-25, y-20), (x+25, y+20)], fill=STATUS_MANAGER)
    for i, (x, y) in enumerate(manager_positions[4:], 4):  # Last 2 pending
        draw.ellipse([(x-25, y-20), (x+25, y+20)], fill=BORDER_SUBTLE)

    # Workers (active = cyan, done = green)
    worker_positions = []
    for mx in [480, 560, 640, 720, 800, 880]:
        for wy in [490, 510, 530]:
            worker_positions.append((mx, wy))

    for i, (x, y) in enumerate(worker_positions[:12]):  # 12 running
        draw.ellipse([(x-10, y-8), (x+10, y+8)], fill=ACCENT_CYAN if i % 3 != 2 else STATUS_ACTIVE)
    for i, (x, y) in enumerate(worker_positions[12:], 12):  # 6 pending
        draw.ellipse([(x-10, y-8), (x+10, y+8)], fill=BORDER_SUBTLE)

    # Streaming output panel
    draw.rounded_rectangle([(40, 570), (width-40, 750)], radius=8, fill=BG_SECONDARY)
    draw.text((60, 590), "Streaming Output:", font=ImageFont.load_default(), fill=TEXT_SECONDARY)

    # Sample output text (truncated)
    output_lines = [
        "[literature] Searching arXiv for LoRA papers...",
        "[technical] Analyzing QLoRA implementation details...",
        "[variants] Found 12 fine-tuning variants...",
        "[worker_0] Completed: 15 papers analyzed...",
        "[worker_3] Completed: RLHF benchmark comparison..."
    ]
    for i, line in enumerate(output_lines):
        draw.text((60, 615 + i*25), line, font=ImageFont.load_default(),
                  fill=STATUS_ACTIVE if i == len(output_lines)-1 else TEXT_SECONDARY)

    output_path = r"C:\Users\HP\Desktop\Temp while my comp is at the shop\dynamic-graph-compiler\web-demo\static\img\demo-workflow.png"
    img.save(output_path, "PNG", quality=95)
    print(f"Created workflow demo: {output_path}")
    return output_path

def create_demo_output():
    """Create demo-output.png showing the final synthesis report."""
    width, height = 1280, 900
    img = create_gradient_background(width, height, BG_DEEP, BG_PRIMARY)
    draw = ImageDraw.Draw(img)

    # Document frame
    draw.rounded_rectangle([(50, 30), (width-50, height-30)], radius=12,
                           fill=(26, 29, 38), outline=BORDER_SUBTLE, width=1)

    # Header
    draw.rectangle([(70, 50), (width-70, 120)], fill=BG_SECONDARY)
    draw.text(((width)//2, 70), "DynamicAnythingMachine — Execution Report",
              font=ImageFont.load_default(), fill=TEXT_BRIGHT, anchor="mt")
    draw.text(((width)//2, 100), "Goal: Research LLM fine-tuning",
              font=ImageFont.load_default(), fill=TEXT_SECONDARY, anchor="mt")

    # Stats summary bar
    stats_y = 140
    stats = [
        ("28", "Nodes"),
        ("6", "Seams"),
        ("18", "Workers"),
        ("7", "Iterations"),
        ("6,357", "Chars")
    ]
    x_start = 100
    for i, (value, label) in enumerate(stats):
        x = x_start + i * 200
        draw.text((x, stats_y), value, font=ImageFont.load_default(),
                  fill=ACCENT_CYAN, anchor="lm")
        draw.text((x, stats_y + 25), label, font=ImageFont.load_default(),
                  fill=TEXT_MUTED, anchor="lm")

    # Report sections
    sections = [
        ("Literature Review", ["• LoRA: Low-Rank Adaptation for LLMs (Hu et al.)",
                               "• QLoRA: Efficient finetuning for quantized LLMs",
                               "• RLHF: Reinforcement Learning from Human Feedback"]),
        ("Technical Analysis", ["• LoRA reduces trainable parameters by 100x",
                               "• QLoRA enables 4-bit quantization with no quality loss",
                               "• Adapter-based approaches preserve pretrained weights"]),
        ("Implementation", ["• Hugging Face PEFT library supports LoRA natively",
                           "•llama.cpp supports QLoRA inference on consumer GPU",
                           "• Cloud training options: AWS SageMaker, Azure ML"]),
    ]

    y_pos = 190
    for section_title, bullets in sections:
        draw.text((90, y_pos), section_title,
                  font=ImageFont.load_default(), fill=STATUS_SYNTHESIS)
        y_pos += 30
        for bullet in bullets:
            draw.text((110, y_pos), bullet,
                      font=ImageFont.load_default(), fill=TEXT_PRIMARY)
            y_pos += 25
        y_pos += 20

    # Footer
    draw.rectangle([(70, height-80), (width-70, height-50)], fill=BG_SECONDARY)
    draw.text(((width)//2, height-65), "Generated by DynamicAnythingMachine · MiniMax M2.7",
              font=ImageFont.load_default(), fill=TEXT_MUTED, anchor="mt")

    output_path = r"C:\Users\HP\Desktop\Temp while my comp is at the shop\dynamic-graph-compiler\web-demo\static\img\demo-output.png"
    img.save(output_path, "PNG", quality=95)
    print(f"Created output demo: {output_path}")
    return output_path

def create_demo_landing():
    """Create demo-landing.png showing the landing page with architecture."""
    width, height = 1280, 800
    img = create_gradient_background(width, height, BG_DEEP, BG_PRIMARY)
    draw = ImageDraw.Draw(img)

    # Simulate the landing page hero
    draw.rectangle([(0, 0), (width, 100)], fill=BG_SECONDARY)
    draw.text((60, 50), "DynamicAnythingMachine",
              font=ImageFont.load_default(), fill=TEXT_BRIGHT, anchor="lm")

    # Architecture preview placeholder
    draw.rounded_rectangle([(60, 130), (width-60, 400)], radius=12, fill=BG_SECONDARY)
    draw.text(((width)//2, 200), "Architecture SVG Preview",
              font=ImageFont.load_default(), fill=TEXT_MUTED, anchor="mm")
    draw.text(((width)//2, 240), "(Animated SVG with two-phase flow)",
              font=ImageFont.load_default(), fill=TEXT_MUTED, anchor="mm")

    # Stats section
    draw.rounded_rectangle([(60, 430), (width-60, 530)], radius=8, fill=BG_SECONDARY)
    stats = [
        ("28", "Nodes", ACCENT_CYAN),
        ("6", "Seams", STATUS_MANAGER),
        ("18", "Workers", ACCENT_CYAN),
        ("2", "Phases", STATUS_SYNTHESIS)
    ]
    x_start = 150
    for i, (value, label, color) in enumerate(stats):
        x = x_start + i * 280
        draw.text((x, 460), value, font=ImageFont.load_default(), fill=color, anchor="mm")
        draw.text((x, 500), label, font=ImageFont.load_default(), fill=TEXT_SECONDARY, anchor="mm")

    # Features list
    features = [
        "• Dynamic graph construction from natural language",
        "• Two-phase architecture (analysis → execution)",
        "• Hierarchical agent model (orchestrator → manager → worker)",
        "• Per-seam token budgets prevent runaway workers",
        "• Checkpoint-based state recovery",
        "• Multi-provider router with automatic fallback"
    ]
    y_pos = 560
    for feature in features:
        draw.text((100, y_pos), feature, font=ImageFont.load_default(), fill=TEXT_PRIMARY)
        y_pos += 30

    output_path = r"C:\Users\HP\Desktop\Temp while my comp is at the shop\dynamic-graph-compiler\web-demo\static\img\demo-landing.png"
    img.save(output_path, "PNG", quality=95)
    print(f"Created landing demo: {output_path}")
    return output_path

if __name__ == "__main__":
    # Ensure output directory exists
    output_dir = r"C:\Users\HP\Desktop\Temp while my comp is at the shop\dynamic-graph-compiler\web-demo\static\img"
    os.makedirs(output_dir, exist_ok=True)

    print("Generating demo assets...")
    create_og_image()
    create_demo_workflow()
    create_demo_output()
    create_demo_landing()
    print("\nAll demo assets created successfully!")