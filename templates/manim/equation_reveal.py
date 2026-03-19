"""Equation reveal animation pattern."""

from manim import *


class EquationReveal(Scene):
    """Reveal a LaTeX equation with step-by-step highlighting."""

    def construct(self):
        self.camera.background_color = "#1a1a2e"

        # Example: Attention equation
        label = Text("Scaled Dot-Product Attention", font_size=36, color=GREY_B)
        label.to_edge(UP, buff=1)

        equation = MathTex(
            r"\text{Attention}(Q, K, V)",
            r"=",
            r"\text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)",
            r"V",
            font_size=48,
        )

        self.play(Write(label), run_time=1)
        self.wait(0.5)

        # Reveal parts sequentially
        self.play(Write(equation[0]), run_time=1.5)  # Attention(Q,K,V)
        self.play(Write(equation[1]), run_time=0.5)  # =
        self.play(Write(equation[2]), run_time=2)  # softmax(...)
        self.play(Write(equation[3]), run_time=0.8)  # V

        # Highlight the scaling factor
        highlight = SurroundingRectangle(equation[2], color=YELLOW, buff=0.15)
        self.play(Create(highlight), run_time=0.8)
        self.wait(1.5)

        self.play(
            FadeOut(highlight),
            FadeOut(equation),
            FadeOut(label),
            run_time=1,
        )
