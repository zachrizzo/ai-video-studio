"""Base Manim scene template with 3Blue1Brown-style dark theme."""

from manim import *


# 3Blue1Brown color palette
PALETTE = {
    "bg": "#1a1a2e",
    "blue": BLUE_C,
    "yellow": YELLOW,
    "green": GREEN_C,
    "red": RED_C,
    "purple": PURPLE_C,
    "teal": TEAL_C,
    "orange": ORANGE,
    "grey": GREY_B,
}


class BaseScene(Scene):
    """Base scene with 3B1B-style defaults."""

    def construct(self):
        self.camera.background_color = "#1a1a2e"
        self.build()

    def build(self):
        """Override this in subclasses."""
        pass

    def clear_screen(self, run_time=0.5):
        """Fade out all objects on screen."""
        if self.mobjects:
            self.play(FadeOut(*self.mobjects), run_time=run_time)

    def section_title(self, text: str, run_time=2.0):
        """Display a section title then fade it out."""
        title = Text(text, font_size=56, color=WHITE)
        self.play(Write(title), run_time=run_time * 0.6)
        self.wait(run_time * 0.2)
        self.play(FadeOut(title), run_time=run_time * 0.2)
