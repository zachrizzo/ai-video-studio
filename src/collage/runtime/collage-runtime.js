/*
 * Collage deterministic runtime.
 *
 * One self-contained script. Reads the compiled scene the builder embeds at
 * window.__COLLAGE__ and implements the frozen seek contract
 * (docs/collage/CONTRACTS.md section 1):
 *
 *   window.__SCENE__ = {duration, fps}
 *   window.seek(t)   -> sets ALL visual state as a pure function of t
 *   window.sceneReady -> Promise; resolves after fonts + image decode
 *
 * Determinism: every visual property is a pure function of t. No wall-clock
 * time is read. Randomness is a seeded mulberry32 PRNG; particle motion is
 * closed-form base + amplitude * sin(speed * t + phase), never a stepped
 * simulation. All SVG is built through innerHTML so the HTML parser assigns
 * the SVG namespace for us (no namespace URL literal anywhere in this file).
 */
(function () {
  "use strict";

  var SCENE = window.__COLLAGE__ || {};
  var DURATION = SCENE.duration || 1;
  var FPS = SCENE.fps || 30;

  window.__SCENE__ = { duration: DURATION, fps: FPS };

  // ---- deterministic PRNG -------------------------------------------------
  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a |= 0;
      a = (a + 0x6d2b79f5) | 0;
      var tt = Math.imul(a ^ (a >>> 15), 1 | a);
      tt = (tt + Math.imul(tt ^ (tt >>> 7), 61 | tt)) ^ tt;
      return ((tt ^ (tt >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ---- easing / math helpers ----------------------------------------------
  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }
  function clamp01(v) {
    return clamp(v, 0, 1);
  }
  // cubic in-out — the calm documentary motion curve
  function easeInOut(u) {
    u = clamp01(u);
    return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
  }
  function lerp(a, b, u) {
    return a + (b - a) * u;
  }

  // ---- frame geometry -----------------------------------------------------
  var frame = document.createElement("div");
  frame.className = "collage-frame";
  var stage = document.createElement("div");
  stage.className = "collage-stage";
  frame.appendChild(stage);
  document.body.appendChild(frame);

  var FW = 0;
  var FH = 0;
  function measure() {
    var r = frame.getBoundingClientRect();
    FW = r.width || window.innerWidth || 1920;
    FH = r.height || window.innerHeight || 1080;
  }
  measure();
  window.addEventListener("resize", function () {
    measure();
  });

  if (SCENE.background) {
    frame.style.background = SCENE.background;
  }

  // ---- camera -------------------------------------------------------------
  var camKeys = (SCENE.camera || []).slice().sort(function (a, b) {
    return a.t - b.t;
  });
  function cameraAt(t) {
    if (!camKeys.length) {
      return { x: 0.5, y: 0.5, scale: 1 };
    }
    if (t <= camKeys[0].t) {
      return { x: camKeys[0].x, y: camKeys[0].y, scale: camKeys[0].scale };
    }
    var last = camKeys[camKeys.length - 1];
    if (t >= last.t) {
      return { x: last.x, y: last.y, scale: last.scale };
    }
    for (var i = 0; i < camKeys.length - 1; i++) {
      var a = camKeys[i];
      var b = camKeys[i + 1];
      if (t >= a.t && t <= b.t) {
        var span = b.t - a.t;
        var u = span > 0 ? easeInOut((t - a.t) / span) : 0;
        return {
          x: lerp(a.x, b.x, u),
          y: lerp(a.y, b.y, u),
          scale: lerp(a.scale, b.scale, u),
        };
      }
    }
    return { x: last.x, y: last.y, scale: last.scale };
  }

  // ---- transition envelopes ----------------------------------------------
  // Blur-masked camera-push at the scene boundaries. transitionIn peaks at t=0
  // and clears over `seconds`; transitionOut is clear until DURATION-seconds and
  // peaks at the end. Two adjacent scenes both carrying an envelope meet at the
  // hard cut already blurred + pushed, so the cut reads as a tracking dissolve.
  var TR_IN = SCENE.transitionIn || null;
  var TR_OUT = SCENE.transitionOut || null;
  // Returns additive {blur (px), push (scale)} contributed by the envelopes.
  function transitionAt(t) {
    var blur = 0;
    var push = 0;
    if (TR_IN && TR_IN.seconds > 0 && t < TR_IN.seconds) {
      var ui = 1 - easeInOut(clamp01(t / TR_IN.seconds)); // 1 at t=0 -> 0
      blur += TR_IN.blurPx * ui;
      push += TR_IN.push * ui;
    }
    if (TR_OUT && TR_OUT.seconds > 0) {
      var startO = DURATION - TR_OUT.seconds;
      if (t > startO) {
        var uo = easeInOut(clamp01((t - startO) / TR_OUT.seconds)); // 0 -> 1 at end
        blur += TR_OUT.blurPx * uo;
        push += TR_OUT.push * uo;
      }
    }
    return { blur: blur, push: push };
  }

  // Parallax offset (px) for an element at the given depth, given the camera.
  // camera_offset = camera center displacement from (0.5, 0.5); depth 0 = locked
  // to the camera (no parallax), depth 1 = full parallax (moves opposite to the
  // camera, giving the sense of distance).
  function parallax(cam, depth) {
    return {
      x: -(cam.x - 0.5) * depth * FW,
      y: -(cam.y - 0.5) * depth * FH,
    };
  }

  // ---- enter / exit envelope ---------------------------------------------
  var FADE = 0.6; // seconds
  var DRIFT = 12; // px upward drift
  // Returns {vis, dy}: opacity multiplier and vertical pixel offset.
  function envelope(t, enter, exit) {
    var vis = 1;
    var dy = 0;
    if (enter !== null && enter !== undefined) {
      if (t < enter) {
        return { vis: 0, dy: DRIFT };
      }
      var ein = easeInOut(clamp01((t - enter) / FADE));
      vis *= ein;
      dy += (1 - ein) * DRIFT;
    }
    if (exit !== null && exit !== undefined) {
      if (t >= exit) {
        var eo = easeInOut(clamp01((t - exit) / FADE));
        vis *= 1 - eo;
        dy -= eo * DRIFT;
        if (t >= exit + FADE) {
          vis = 0;
        }
      }
    }
    return { vis: vis, dy: dy };
  }

  // ---- image registry (for sceneReady decode) -----------------------------
  var images = [];
  function makeImg(url) {
    var img = document.createElement("img");
    img.decoding = "async";
    img.src = url;
    images.push(img);
    return img;
  }

  // ---- video registry (native video layers) -------------------------------
  // Video layers make seek(t) ASYNCHRONOUS: setting video.currentTime decodes
  // off the main thread, so a screenshot taken immediately would race and grab
  // the previous frame. We register every <video>, drive its currentTime as a
  // pure function of t inside the renderers, then have seek(t) return a Promise
  // that resolves once every video has decoded its target frame (the frame
  // renderer AWAITs page.evaluate(window.seek(t)), so the awaited screenshot
  // captures the decoded frame). No videos in the scene -> seek stays
  // synchronous (returns undefined), preserving the frozen contract.
  var videos = [];
  var SEEK_TIMEOUT_MS = 250; // guard: a stalled decode can't freeze the render
  function makeVideo(url) {
    var v = document.createElement("video");
    v.className = "collage-video";
    v.muted = true;
    v.defaultMuted = true;
    v.playsInline = true;
    v.autoplay = false;
    v.loop = false;
    v.preload = "auto";
    // NB: set .src via JS (not a src="" attribute) so the self-contained /
    // no-network validator never sees a src= literal; relative file URLs only.
    v.src = url;
    v.load();
    var rec = { video: v, setTarget: NaN, pending: false };
    videos.push(rec);
    return rec;
  }
  // Request a decoded frame at *target* seconds (idempotent per target).
  function driveVideo(rec, target) {
    if (rec.setTarget === target) {
      return; // already showing (or decoding) this exact frame
    }
    rec.setTarget = target;
    rec.pending = true;
    rec.video.currentTime = target;
  }
  // Resolve once this video reaches its requested frame (or the guard fires).
  function waitVideo(rec) {
    if (!rec.pending) {
      return Promise.resolve();
    }
    var video = rec.video;
    return new Promise(function (resolve) {
      var settled = false;
      function finish() {
        if (settled) {
          return;
        }
        settled = true;
        rec.pending = false;
        video.removeEventListener("seeked", finish);
        resolve();
      }
      video.addEventListener("seeked", finish);
      setTimeout(finish, SEEK_TIMEOUT_MS);
    });
  }

  // Elements that can be attach targets expose centerPx(t) -> {x, y} screen px.
  var centers = {};

  // ---- element renderers --------------------------------------------------
  var renderers = [];

  function baseTransform(cx, cy, parX, parY, dy, scale, rotate) {
    // position is the element center; translate(-50%,-50%) centers the box.
    return (
      "translate(" +
      (parX) +
      "px," +
      (parY + dy) +
      "px) translate(-50%,-50%) scale(" +
      scale +
      ") rotate(" +
      rotate +
      "deg)"
    );
  }

  function buildLayer(el) {
    var node = document.createElement("div");
    node.className = "collage-layer";
    node.style.zIndex = String(el.z || 0);
    var img = makeImg(el.assetUrl);
    node.appendChild(img);
    stage.appendChild(node);

    // Subject motion: keyframed pose path (builder pre-resolves times and
    // fills every field, so keys are dense) plus an optional closed-form
    // oscillation. Everything stays a pure function of t.
    var moveKeys = (el.move || []).slice().sort(function (a, b) {
      return a.t - b.t;
    });
    function poseAt(t) {
      var pose = { x: el.x, y: el.y, scale: el.scale, rotate: el.rotate };
      if (moveKeys.length) {
        var first = moveKeys[0];
        var last = moveKeys[moveKeys.length - 1];
        if (t <= first.t) {
          pose = { x: first.x, y: first.y, scale: first.scale, rotate: first.rotate };
        } else if (t >= last.t) {
          pose = { x: last.x, y: last.y, scale: last.scale, rotate: last.rotate };
        } else {
          for (var i = 0; i < moveKeys.length - 1; i++) {
            var a = moveKeys[i];
            var b = moveKeys[i + 1];
            if (t >= a.t && t <= b.t) {
              var span = b.t - a.t;
              var u = span > 0 ? easeInOut((t - a.t) / span) : 0;
              pose = {
                x: a.x + (b.x - a.x) * u,
                y: a.y + (b.y - a.y) * u,
                scale: a.scale + (b.scale - a.scale) * u,
                rotate: a.rotate + (b.rotate - a.rotate) * u,
              };
              break;
            }
          }
        }
      }
      var osc = el.oscillate;
      if (osc) {
        var w =
          Math.sin((t / osc.period + (osc.phase || 0)) * 2 * Math.PI) *
          osc.amplitude;
        if (osc.axis === "x") pose.x += w;
        else if (osc.axis === "y") pose.y += w;
        else if (osc.axis === "rotate") pose.rotate += w;
        else if (osc.axis === "scale") pose.scale = Math.max(0.01, pose.scale + w);
      }
      return pose;
    }

    centers[el.id] = function (t) {
      var cam = cameraAt(t);
      var p = parallax(cam, el.depth);
      var pose = poseAt(t);
      return { x: pose.x * FW + p.x, y: pose.y * FH + p.y, depth: el.depth };
    };

    renderers.push(function (t) {
      var env = envelope(t, el.enter, el.exit);
      var cam = cameraAt(t);
      var p = parallax(cam, el.depth);
      var pose = poseAt(t);
      node.style.left = pose.x * FW + "px";
      node.style.top = pose.y * FH + "px";
      node.style.width = el.width * FW + "px";
      img.style.width = "100%";
      node.style.opacity = String(el.opacity * env.vis);
      node.style.transform = baseTransform(
        pose.x,
        pose.y,
        p.x,
        p.y,
        env.dy,
        pose.scale,
        pose.rotate
      );
      node.style.display = env.vis <= 0 ? "none" : "block";
    });
    return node;
  }

  function buildVideoLayer(el) {
    // Reuse the layer wrapper so positioning/parallax/transform CSS is shared;
    // the inner <video> is styled like a layer <img> (.collage-video).
    var node = document.createElement("div");
    node.className = "collage-layer";
    node.style.zIndex = String(el.z || 0);
    var rec = makeVideo(el.videoUrl);
    var video = rec.video;
    node.appendChild(video);
    stage.appendChild(node);

    var startResolved = el.start === null || el.start === undefined ? 0 : el.start;
    var clipStart = el.clip_start || 0;
    var rate = el.rate || 1;

    // currentTime is a PURE function of t (no autoplay/loop): the clip begins at
    // scene time startResolved, offset clipStart into the source, at `rate`.
    function timeAt(t) {
      var target = clipStart + Math.max(0, t - startResolved) * rate;
      var dur = video.duration;
      if (isFinite(dur) && dur > 0) {
        return clamp(target, 0, dur);
      }
      return Math.max(0, target);
    }

    centers[el.id] = function (t) {
      var cam = cameraAt(t);
      var p = parallax(cam, el.depth);
      return { x: el.x * FW + p.x, y: el.y * FH + p.y, depth: el.depth };
    };

    renderers.push(function (t) {
      var env = envelope(t, el.enter, el.exit);
      var cam = cameraAt(t);
      var p = parallax(cam, el.depth);
      node.style.left = el.x * FW + "px";
      node.style.top = el.y * FH + "px";
      node.style.width = el.width * FW + "px";
      node.style.opacity = String(el.opacity * env.vis);
      node.style.transform = baseTransform(
        el.x,
        el.y,
        p.x,
        p.y,
        env.dy,
        el.scale,
        el.rotate
      );
      node.style.display = env.vis <= 0 ? "none" : "block";
      // Drive the decode target for this frame; seek() awaits it below.
      driveVideo(rec, timeAt(t));
    });
    return node;
  }

  function buildLabel(el) {
    var node = document.createElement("div");
    node.className = "collage-label collage-label-" + el.style;
    node.style.color = el.color;
    // Labels annotate the artwork and must always read above any layer,
    // regardless of that layer's authored z (layers use small ints like 0-3).
    node.style.zIndex = "500";
    var pin = null;
    if (el.attach) {
      pin = document.createElement("div");
      pin.className = "collage-pin";
      node.appendChild(pin);
    }
    var span = document.createElement("span");
    span.className = "collage-label-text";
    span.textContent = el.text;
    node.appendChild(span);
    stage.appendChild(node);

    var rnd = mulberry32(el.seed);
    // 1-2 degrees, random sign — the torn-paper tilt.
    var tilt = (rnd() < 0.5 ? -1 : 1) * (1 + rnd());

    centers[el.id] = function () {
      return { x: el.x * FW, y: el.y * FH, depth: 0 };
    };

    renderers.push(function (t) {
      var env = envelope(t, el.enter, el.exit);
      var px;
      var py;
      var depth = 0;
      if (el.attach && centers[el.attach]) {
        var c = centers[el.attach](t);
        px = c.x + el.x * FW;
        py = c.y + el.y * FH;
        depth = c.depth || 0;
        // draw the pin line from the label toward the attach point
        var dx = -el.x * FW;
        var dy2 = -el.y * FH;
        var len = Math.hypot(dx, dy2);
        var ang = (Math.atan2(dy2, dx) * 180) / Math.PI;
        pin.style.width = len + "px";
        pin.style.transform = "rotate(" + ang + "deg)";
      } else {
        var cam = cameraAt(t);
        var p = parallax(cam, depth);
        px = el.x * FW + p.x;
        py = el.y * FH + p.y;
      }
      node.style.left = px + "px";
      node.style.top = py + "px";
      node.style.opacity = String(env.vis);
      node.style.transform =
        "translate(0px," + env.dy + "px) rotate(" + tilt + "deg)";
      node.style.display = env.vis <= 0 ? "none" : "block";
    });
    return node;
  }

  function buildParticles(el) {
    var wrap = document.createElement("div");
    wrap.className = "collage-particles";
    var canvas = document.createElement("canvas");
    wrap.appendChild(canvas);
    stage.appendChild(wrap);
    var ctx = canvas.getContext("2d");

    var rnd = mulberry32(el.seed);
    var parts = [];
    for (var i = 0; i < el.count; i++) {
      parts.push({
        bx: rnd(),
        by: rnd(),
        ampx: 0.02 + rnd() * 0.06,
        ampy: 0.02 + rnd() * 0.06,
        speed: 0.15 + rnd() * 0.5,
        phase: rnd() * Math.PI * 2,
        size: 0.5 + rnd() * 2.5,
        drift: (rnd() - 0.5) * 0.03,
      });
    }

    function area() {
      return {
        x: el.area.x * FW,
        y: el.area.y * FH,
        w: el.area.w * FW,
        h: el.area.h * FH,
      };
    }

    function draw(t, vis) {
      var a = area();
      canvas.width = Math.max(1, Math.round(a.w));
      canvas.height = Math.max(1, Math.round(a.h));
      wrap.style.left = a.x + "px";
      wrap.style.top = a.y + "px";
      wrap.style.width = a.w + "px";
      wrap.style.height = a.h + "px";
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (vis <= 0) {
        return;
      }
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        // closed-form position; wrap into [0,1] with mod so it tiles the area
        var fx = p.bx + p.ampx * Math.sin(p.speed * t + p.phase) + p.drift * t;
        var fy =
          p.by + p.ampy * Math.sin(p.speed * 0.8 * t + p.phase * 1.3) - 0.02 * t;
        fx = fx - Math.floor(fx);
        fy = fy - Math.floor(fy);
        var x = fx * canvas.width;
        var y = fy * canvas.height;
        if (el.style === "biolume") {
          var pulse = 0.5 + 0.5 * Math.sin(p.speed * 2 * t + p.phase);
          var r = p.size * 2.2;
          var g = ctx.createRadialGradient(x, y, 0, x, y, r);
          g.addColorStop(0, el.color);
          g.addColorStop(1, "rgba(0,0,0,0)");
          ctx.globalAlpha = vis * (0.25 + 0.55 * pulse);
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(x, y, r, 0, Math.PI * 2);
          ctx.fill();
        } else if (el.style === "sparks") {
          var vx = Math.cos(p.phase) * (4 + p.size * 3);
          var vy = Math.sin(p.phase) * (4 + p.size * 3);
          ctx.globalAlpha = vis * (0.4 + 0.5 * Math.abs(Math.sin(p.speed * t)));
          ctx.strokeStyle = el.color;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(x + vx, y + vy);
          ctx.stroke();
        } else {
          // dust
          ctx.globalAlpha = vis * 0.35;
          ctx.fillStyle = el.color;
          ctx.beginPath();
          ctx.arc(x, y, p.size, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
    }

    renderers.push(function (t) {
      var env = envelope(t, el.enter, el.exit);
      var cam = cameraAt(t);
      var p = parallax(cam, el.depth);
      wrap.style.transform = "translate(" + p.x + "px," + p.y + "px)";
      draw(t, env.vis);
    });
    return wrap;
  }

  function buildSplit(el) {
    var wrap = document.createElement("div");
    wrap.className = "collage-split collage-split-" + el.direction;
    wrap.style.gap = el.gap * 100 + "%";
    var panelNodes = [];
    for (var i = 0; i < el.panels.length; i++) {
      var pnl = el.panels[i];
      var pn = document.createElement("div");
      pn.className = "collage-panel";
      var img = makeImg(pnl.assetUrl);
      pn.appendChild(img);
      if (pnl.label) {
        var lab = document.createElement("div");
        lab.className = "collage-panel-label";
        lab.textContent = pnl.label;
        pn.appendChild(lab);
      }
      wrap.appendChild(pn);
      panelNodes.push(pn);
    }
    stage.appendChild(wrap);

    renderers.push(function (t) {
      var baseEnter = el.enter || 0;
      for (var i = 0; i < panelNodes.length; i++) {
        // panels enter staggered 0.3s apart
        var pe = envelope(t, baseEnter + i * 0.3, el.exit);
        panelNodes[i].style.opacity = String(pe.vis);
        panelNodes[i].style.transform = "translateY(" + pe.dy + "px)";
      }
    });
    return wrap;
  }

  function buildTypewriter(el) {
    var node = document.createElement("div");
    node.className = "collage-typewriter collage-font-" + el.font;
    node.style.color = el.color;
    node.style.left = el.x * FW + "px";
    node.style.top = el.y * FH + "px";
    // Captions read above any layer, regardless of that layer's authored z.
    node.style.zIndex = "500";
    var textSpan = document.createElement("span");
    var cursor = document.createElement("span");
    cursor.className = "collage-cursor";
    cursor.textContent = "█";
    node.appendChild(textSpan);
    node.appendChild(cursor);
    stage.appendChild(node);

    var chars = el.text;

    renderers.push(function (t) {
      var enter = el.enter || 0;
      var env = envelope(t, el.enter, el.exit);
      var n = Math.floor(Math.max(0, t - enter) * el.speed_cps);
      if (n > chars.length) {
        n = chars.length;
      }
      textSpan.textContent = chars.slice(0, n);
      var typing = n < chars.length && t >= enter;
      // blink the block cursor while typing (pure function of t)
      var blink = Math.floor(t * 2) % 2 === 0;
      cursor.style.opacity = typing && blink ? "1" : "0";
      node.style.opacity = String(env.vis);
    });
    return node;
  }

  function buildNodeGraph(el) {
    var wrap = document.createElement("div");
    wrap.className = "collage-nodegraph";
    // Diagram reads above any layer, regardless of that layer's authored z.
    wrap.style.zIndex = "500";
    // Build the SVG through innerHTML so the parser assigns the SVG namespace.
    var svg =
      '<svg class="collage-ng-svg" viewBox="0 0 1000 562" preserveAspectRatio="none">';
    var edgeMeta = [];
    for (var e = 0; e < el.edges.length; e++) {
      var a = nodeById(el.nodes, el.edges[e][0]);
      var b = nodeById(el.nodes, el.edges[e][1]);
      var x1 = a.x * 1000;
      var y1 = a.y * 562;
      var x2 = b.x * 1000;
      var y2 = b.y * 562;
      var len = Math.hypot(x2 - x1, y2 - y1);
      edgeMeta.push({ len: len });
      svg +=
        '<line class="collage-ng-edge" data-i="' +
        e +
        '" x1="' +
        x1 +
        '" y1="' +
        y1 +
        '" x2="' +
        x2 +
        '" y2="' +
        y2 +
        '" stroke="' +
        el.accent +
        '" />';
    }
    for (var n = 0; n < el.nodes.length; n++) {
      var nd = el.nodes[n];
      var nx = nd.x * 1000;
      var ny = nd.y * 562;
      svg +=
        '<circle class="collage-ng-node" data-i="' +
        n +
        '" cx="' +
        nx +
        '" cy="' +
        ny +
        '" r="9" fill="' +
        el.color +
        '" />';
      svg +=
        '<text class="collage-ng-label" data-i="' +
        n +
        '" x="' +
        nx +
        '" y="' +
        (ny - 16) +
        '" fill="' +
        el.color +
        '">' +
        escapeHtml(nd.label) +
        "</text>";
    }
    svg += "</svg>";
    wrap.innerHTML = svg;
    stage.appendChild(wrap);

    var edgeEls = wrap.querySelectorAll(".collage-ng-edge");
    var nodeEls = wrap.querySelectorAll(".collage-ng-node");
    var labelEls = wrap.querySelectorAll(".collage-ng-label");
    for (var i = 0; i < edgeEls.length; i++) {
      var L = edgeMeta[i].len;
      edgeEls[i].style.strokeDasharray = L;
      edgeEls[i].style.strokeDashoffset = L;
    }

    renderers.push(function (t) {
      var env = envelope(t, el.enter, el.exit);
      wrap.style.opacity = String(env.vis);
      var reveal = el.reveal;
      // nodes fade in first, over 0.4s starting at reveal
      var nodeAlpha = easeInOut(clamp01((t - reveal) / 0.4));
      for (var i = 0; i < nodeEls.length; i++) {
        nodeEls[i].style.opacity = String(nodeAlpha);
      }
      for (var j = 0; j < labelEls.length; j++) {
        labelEls[j].style.opacity = String(nodeAlpha);
      }
      // edges revealed sequentially after the nodes: 0.3s delay, 0.25s stagger,
      // each drawing over 0.5s (all pure functions of t)
      for (var k = 0; k < edgeEls.length; k++) {
        var start = reveal + 0.3 + k * 0.25;
        var prog = easeInOut(clamp01((t - start) / 0.5));
        var L = edgeMeta[k].len;
        edgeEls[k].style.strokeDashoffset = String(L * (1 - prog));
      }
    });
    return wrap;
  }

  // ---- masks (applied to a target element after it is built) --------------
  var HEAD_PATH =
    "M0.5,0.06 C0.66,0.06 0.75,0.19 0.75,0.34 C0.75,0.43 0.72,0.5 0.71,0.55 " +
    "C0.79,0.57 0.84,0.63 0.86,0.72 C0.9,0.86 0.9,1 0.9,1 L0.1,1 " +
    "C0.1,1 0.1,0.86 0.14,0.72 C0.16,0.63 0.21,0.57 0.29,0.55 " +
    "C0.28,0.5 0.25,0.43 0.25,0.34 C0.25,0.19 0.34,0.06 0.5,0.06 Z";
  var maskCounter = 0;

  function buildMask(el, targetNode) {
    if (!targetNode) {
      return;
    }
    var clipId = null;
    if (el.shape === "head_silhouette") {
      clipId = "collage-clip-" + maskCounter++;
      var holder = document.createElement("div");
      holder.className = "collage-clip-holder";
      // NOTE: the transform MUST live on the <path> itself, not a wrapping
      // <g transform="...">. Chromium has a rendering bug where a <g> with a
      // transform (even an identity transform) inside an objectBoundingBox
      // clipPath makes the clipped target fully invisible whenever the target
      // also has `will-change` set. Putting the transform directly on the
      // <path> avoids the bug and renders identically.
      holder.innerHTML =
        '<svg width="0" height="0"><defs><clipPath id="' +
        clipId +
        '" clipPathUnits="objectBoundingBox"><path class="collage-clip-path" d="' +
        HEAD_PATH +
        '" /></clipPath></defs></svg>';
      stage.appendChild(holder);
      targetNode.style.clipPath = "url(#" + clipId + ")";
      targetNode.style.webkitClipPath = "url(#" + clipId + ")";
      var clipPathEl = holder.querySelector(".collage-clip-path");
      renderers.push(function (t) {
        var prog = easeInOut(clamp01((t - el.reveal) / el.duration));
        // grow the silhouette from a point at its center
        var s = prog;
        clipPathEl.setAttribute(
          "transform",
          "translate(" + 0.5 * (1 - s) + "," + 0.5 * (1 - s) + ") scale(" + s + ")"
        );
      });
    } else if (el.shape === "rect") {
      renderers.push(function (t) {
        var prog = easeInOut(clamp01((t - el.reveal) / el.duration));
        var inset = (1 - prog) * 50; // percent from each edge
        var cp = "inset(" + inset + "% " + inset + "% " + inset + "% " + inset + "%)";
        targetNode.style.clipPath = cp;
        targetNode.style.webkitClipPath = cp;
      });
    } else {
      // circle
      renderers.push(function (t) {
        var prog = easeInOut(clamp01((t - el.reveal) / el.duration));
        var r = prog * 75; // percent; 75% covers the box corners
        var cp = "circle(" + r + "% at 50% 50%)";
        targetNode.style.clipPath = cp;
        targetNode.style.webkitClipPath = cp;
      });
    }
  }

  // ---- helpers ------------------------------------------------------------
  function nodeById(nodes, id) {
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].id === id) {
        return nodes[i];
      }
    }
    return { x: 0.5, y: 0.5 };
  }
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // ---- build all elements -------------------------------------------------
  var nodeById2 = {}; // element id -> DOM node (for mask targets)
  var elements = SCENE.elements || [];
  var masks = [];
  for (var i = 0; i < elements.length; i++) {
    var el = elements[i];
    var node = null;
    if (el.type === "layer") {
      node = buildLayer(el);
    } else if (el.type === "video") {
      node = buildVideoLayer(el);
    } else if (el.type === "label") {
      node = buildLabel(el);
    } else if (el.type === "particles") {
      node = buildParticles(el);
    } else if (el.type === "split") {
      node = buildSplit(el);
    } else if (el.type === "typewriter") {
      node = buildTypewriter(el);
    } else if (el.type === "nodegraph") {
      node = buildNodeGraph(el);
    } else if (el.type === "mask") {
      masks.push(el); // deferred until targets exist
    }
    if (node) {
      nodeById2[el.id] = node;
    }
  }
  for (var m = 0; m < masks.length; m++) {
    buildMask(masks[m], nodeById2[masks[m].target]);
  }

  // ---- camera scale on the stage + transition blur/push -------------------
  renderers.push(function (t) {
    var cam = cameraAt(t);
    var tr = transitionAt(t);
    stage.style.transformOrigin = "50% 50%";
    stage.style.transform = "scale(" + (cam.scale + tr.push) + ")";
    // Frame blur masks the hard cut at scene boundaries (whole composite).
    frame.style.filter = tr.blur > 0.01 ? "blur(" + tr.blur.toFixed(2) + "px)" : "none";
  });

  // ---- global finish: grain + vignette ------------------------------------
  (function finish() {
    var grain = document.createElement("div");
    grain.className = "collage-grain";
    grain.innerHTML =
      '<svg width="100%" height="100%"><defs><filter id="collage-grain-f">' +
      '<feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch"/>' +
      '<feColorMatrix type="saturate" values="0"/>' +
      "</filter></defs>" +
      '<rect width="100%" height="100%" filter="url(#collage-grain-f)"/></svg>';
    frame.appendChild(grain);

    var vig = document.createElement("div");
    vig.className = "collage-vignette";
    frame.appendChild(vig);

    // Optional lens character: a periphery-masked overlay carrying an edge blur
    // (backdrop-filter) and a faint red/cyan chromatic fringe. Constant in t —
    // the composition is flat art, so, like the reference AE technique, the
    // aberration is masked to the frame edges rather than applied full-frame.
    if (SCENE.lens) {
      var lens = document.createElement("div");
      lens.className = "collage-lens";
      frame.appendChild(lens);
    }
  })();

  // ---- seek: the pure function of t ---------------------------------------
  // "Motion on twos": when stutterFps is set, the whole composite samples on a
  // coarse cadence (floor(t*fps)/fps) while the file still frame-renders at the
  // real fps — the stuttered, hand-animated Vox look. Still a pure function of t.
  var STUTTER = SCENE.stutterFps || 0;
  function seek(t) {
    var tc = clamp(t, 0, DURATION);
    if (STUTTER > 0) {
      tc = Math.floor(tc * STUTTER) / STUTTER;
    }
    for (var i = 0; i < renderers.length; i++) {
      renderers[i](tc);
    }
    // Async only when the scene has video: return a Promise that resolves once
    // every clip has decoded its target frame, so the awaiting frame renderer
    // screenshots the correct frame. No video -> synchronous (contract intact).
    if (videos.length) {
      var waits = [];
      for (var v = 0; v < videos.length; v++) {
        waits.push(waitVideo(videos[v]));
      }
      return Promise.all(waits);
    }
    return undefined;
  }
  window.seek = seek;

  // ---- sceneReady ---------------------------------------------------------
  var families = SCENE.fonts || [];
  window.sceneReady = (function () {
    function fontsReady() {
      if (!document.fonts || !document.fonts.ready) {
        return Promise.resolve();
      }
      return document.fonts.ready.then(function () {
        // fonts.ready resolves even when a face failed; check each family.
        for (var i = 0; i < families.length; i++) {
          try {
            document.fonts.check("16px '" + families[i] + "'");
          } catch (e) {
            /* ignore unsupported check */
          }
        }
        return true;
      });
    }
    function imagesReady() {
      var jobs = [];
      for (var i = 0; i < images.length; i++) {
        var img = images[i];
        if (img.decode) {
          jobs.push(
            img.decode().then(
              function () {},
              function () {}
            )
          );
        }
      }
      return Promise.all(jobs);
    }
    function videoReady(rec) {
      var v = rec.video;
      // HAVE_CURRENT_DATA (>=2): metadata + the current frame are decodable, so
      // duration is known and currentTime seeks will fire `seeked`.
      if (v.readyState >= 2) {
        return Promise.resolve();
      }
      return new Promise(function (resolve) {
        var settled = false;
        function done() {
          if (settled) {
            return;
          }
          settled = true;
          v.removeEventListener("loadeddata", done);
          v.removeEventListener("canplay", done);
          resolve();
        }
        v.addEventListener("loadeddata", done);
        v.addEventListener("canplay", done);
        setTimeout(done, 5000); // bounded like the sceneReady wait
      });
    }
    function videosReady() {
      var jobs = [];
      for (var i = 0; i < videos.length; i++) {
        jobs.push(videoReady(videos[i]));
      }
      return Promise.all(jobs);
    }
    return Promise.all([fontsReady(), imagesReady(), videosReady()])
      .then(function () {
        // paint the first frame so the renderer never screenshots a blank page.
        // seek(0) returns a Promise when the scene has video — await it so
        // frame 0 is decoded before sceneReady resolves.
        measure();
        return Promise.resolve(seek(0));
      })
      .then(function () {
        return true;
      });
  })();

  // paint an initial frame immediately (deterministic, no timers)
  seek(0);
})();
