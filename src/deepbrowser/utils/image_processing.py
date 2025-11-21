import base64
import importlib.resources
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from deepbrowser.tagging import font as annotation_font


def make_not_available_image() -> str:
    """Create a 'Not Available' image for when browser operations fail or timeout."""
    with (
        BytesIO() as img_buf,
        importlib.resources.path(annotation_font, "firacode.pil"),
    ):
        # Create a new image with white background
        width, height = 640, 480
        background_color = (255, 255, 255)  # White
        image = Image.new("RGB", (width, height), background_color)

        # Create a draw object
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=36)

        # Define the text
        text = "Not available\n(main page load may\nbe paused in proxy\nor timed out)"

        # Calculate text size and position
        text_bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center")
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        text_position = ((width - text_width) // 2, (height - text_height) // 2)

        # Draw the text
        text_color = (0, 0, 0)  # Black
        draw.multiline_text(text_position, text, font=font, fill=text_color, align="center")

        # Save the image
        image.save(img_buf, format="PNG")
        return base64.b64encode(img_buf.getvalue()).decode("utf-8")
