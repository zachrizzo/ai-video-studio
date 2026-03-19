"""Title card scene for video intro/outro."""

from manim import *


class TitleCard(Scene):
    """Animated title card with paper title and author."""

    def construct(self):
        self.camera.background_color = "#1a1a2e"

        # Title
        title = Text(
            "Paper Title Here",
            font_size=56,
            color=WHITE,
            weight=BOLD,
        )
        title.to_edge(UP, buff=2.5)

        # Subtitle / author line
        subtitle = Text(
            "An Educational Exploration",
            font_size=28,
            color=GREY_B,
        )
        subtitle.next_to(title, DOWN, buff=0.6)

        # Decorative line
        line = Line(LEFT * 4, RIGHT * 4, color=BLUE_C, stroke_width=2)
        line.next_to(subtitle, DOWN, buff=0.5)

        # Animate in
        self.play(Write(title), run_time=2)
        self.play(FadeIn(subtitle, shift=UP * 0.3), run_time=1)
        self.play(Create(line), run_time=0.8)
        self.wait(2)
        self.play(
            FadeOut(title),
            FadeOut(subtitle),
            FadeOut(line),
            run_time=1,
        )
