#!/usr/bin/env python3
"""Shared renderer for the reference composition and integration diagrams."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape


COLORS = {
    "background": "#F7F9FC",
    "surface": "#FFFFFF",
    "ink": "#172B4D",
    "muted": "#5E6C84",
    "border": "#C7D1E0",
    "author": "#E8F2FF",
    "resolve": "#EAF7F1",
    "target": "#F2ECFF",
    "runtime": "#FFF4D6",
    "external": "#F1F3F5",
    "planned": "#FFF0F0",
}

RELATIONS = {
    "delivery": {"color": "#4C6FFF", "dash": "", "label": "desired state / artifact"},
    "protocol": {"color": "#00875A", "dash": "10 6", "label": "network protocol / transport"},
    "api": {"color": "#7A5AF8", "dash": "", "label": "API / controller contract"},
    "supply": {"color": "#D96C00", "dash": "4 5", "label": "software supply"},
    "telemetry": {"color": "#147D92", "dash": "12 5 3 5", "label": "telemetry / storage"},
    "trust": {"color": "#C93756", "dash": "2 5", "label": "trust / credential boundary"},
}


def _text(
    x: float,
    y: float,
    value: str,
    css_class: str,
    anchor: str = "start",
    font_size: float | None = None,
) -> str:
    style = f' style="font-size:{font_size:.1f}px"' if font_size is not None else ""
    return (
        f'<text x="{x}" y="{y}" class="{css_class}"{style} '
        f'text-anchor="{anchor}">{escape(value)}</text>'
    )


def _estimated_width(value: str, font_size: float, *, bold: bool = False) -> float:
    """Return a conservative system-font width estimate for fit calculations."""
    factor = 0.60 if bold else 0.56
    return len(value) * font_size * factor


def _fit_font(
    value: str,
    max_width: float,
    preferred: float,
    minimum: float,
    *,
    bold: bool = False,
) -> float:
    estimated = _estimated_width(value, preferred, bold=bold)
    if estimated <= max_width:
        return preferred
    return max(minimum, preferred * max_width / estimated)


def _node(node: dict) -> list[str]:
    x, y, width, height = node["x"], node["y"], node["width"], node["height"]
    fill = COLORS[node.get("kind", "external")]
    content_width = width - 32
    default_title_minimum = 17.4 if node.get("bullets") else 13.5
    title_size = _fit_font(
        node["title"],
        content_width,
        18,
        node.get("min_title_font_size", default_title_minimum),
        bold=True,
    )
    parts = [
        f'<g id="{escape(node["id"])}">',
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="14" '
        f'fill="{fill}" stroke="#7A869A" stroke-width="1.6"/>',
        _text(x + 16, y + 29, node["title"], "node-title", font_size=title_size),
    ]
    line_y = y + 55
    preferred_line_size = node.get("line_font_size", 15)
    minimum_line_size = node.get("min_line_font_size", 11.5)
    minimum_bullet_size = node.get("min_bullet_font_size", 14.9)
    line_gap = node.get("line_gap", 23)
    for line in node.get("lines", []):
        line_size = _fit_font(
            line,
            content_width,
            preferred_line_size,
            minimum_line_size,
        )
        parts.append(_text(x + 16, line_y, line, "node-line", font_size=line_size))
        line_y += line_gap
    for bullet in node.get("bullets", []):
        bullet_size = _fit_font(
            bullet,
            content_width - 20,
            preferred_line_size,
            minimum_bullet_size,
        )
        parts.append(_text(x + 17, line_y, "•", "node-line", font_size=bullet_size))
        parts.append(_text(x + 36, line_y, bullet, "node-line", font_size=bullet_size))
        line_y += line_gap
    if node.get("badge"):
        badge = node["badge"]
        badge_size = _fit_font(badge, width - 48, 11.5, 10, bold=True)
        badge_width = max(76, _estimated_width(badge, badge_size, bold=True) + 20)
        badge_y = y + height - 30
        parts.extend(
            [
                f'<rect x="{x + width - badge_width - 12}" y="{badge_y}" '
                f'width="{badge_width}" height="21" rx="11" fill="#FFFFFF" '
                'stroke="#A5ADBA" stroke-width="1"/>',
                _text(
                    x + width - badge_width / 2 - 12,
                    badge_y + 14.5,
                    badge,
                    "badge",
                    "middle",
                    badge_size,
                ),
            ]
        )
    parts.append("</g>")
    return parts


def _zone(zone: dict) -> list[str]:
    x, y, width, height = zone["x"], zone["y"], zone["width"], zone["height"]
    fill = zone.get("fill", "#FFFFFF")
    fill_opacity = zone.get("fill_opacity", 0.72)
    stroke = zone.get("stroke", "#C7D1E0")
    return [
        f'<g id="zone-{escape(zone["id"])}">',
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="18" '
        f'fill="{fill}" fill-opacity="{fill_opacity}" stroke="{stroke}" '
        'stroke-width="1.5" stroke-dasharray="8 6"/>',
        "</g>",
    ]


def _zone_label(zone: dict) -> list[str]:
    x, y, width = zone["x"], zone["y"], zone["width"]
    content_width = width - 40
    title_size = _fit_font(zone["label"], content_width, 18, 13.5, bold=True)
    label_width = _estimated_width(zone["label"], title_size, bold=True)
    note_size = 0.0
    note_width = 0.0
    if zone.get("note"):
        note_size = _fit_font(zone["note"], content_width, 14, 11.5)
        note_width = _estimated_width(zone["note"], note_size)
    background_width = min(content_width + 16, max(label_width, note_width) + 16)
    background_height = 55 if zone.get("note") else 33
    parts = [
        f'<g id="zone-label-{escape(zone["id"])}">',
        f'<rect x="{x + 12}" y="{y + 9}" width="{background_width}" '
        f'height="{background_height}" rx="6" fill="#FFFFFF" fill-opacity="0.97"/>',
        _text(x + 20, y + 31, zone["label"], "zone-title", font_size=title_size),
    ]
    if zone.get("note"):
        parts.append(_text(x + 20, y + 53, zone["note"], "zone-note", font_size=note_size))
    parts.append("</g>")
    return parts


def _edge_path(edge: dict) -> str:
    relation = RELATIONS[edge["relation"]]
    points = edge["points"]
    path = "M " + " L ".join(f'{x} {y}' for x, y in points)
    marker = f' marker-end="url(#arrow-{edge["relation"]})"' if edge.get("directed") else ""
    dash = f' stroke-dasharray="{relation["dash"]}"' if relation["dash"] else ""
    return (
        f'<path d="{path}" fill="none" stroke="{relation["color"]}" '
        f'stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"{dash}{marker}/>'
    )


def _edge_label(edge: dict) -> list[str]:
    if not edge.get("label"):
        return []
    label_x, label_y = edge["label_at"]
    label = edge["label"]
    label_size = edge.get("label_size", 12.5)
    width = max(36, _estimated_width(label, label_size, bold=True) + 10)
    height = label_size + 8
    return [
        f'<rect x="{label_x - width / 2}" y="{label_y - height + 2}" '
        f'width="{width}" height="{height}" rx="5" fill="#FFFFFF" fill-opacity="0.97" '
        'stroke="#D8DEE8" stroke-width="0.8"/>',
        _text(label_x, label_y, label, "edge-label", "middle", label_size),
    ]


def write_diagram(
    script_file: str,
    output_name: str,
    *,
    title: str,
    subtitle: str,
    description: str,
    width: int,
    height: int,
    zones: list[dict],
    nodes: list[dict],
    edges: list[dict],
    legend: list[str],
) -> Path:
    """Render one semantic diagram and return its SVG path."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        f'<title id="title">{escape(title)}</title>',
        f'<desc id="description">{escape(description)}</desc>',
        "<defs>",
    ]
    for name, relation in RELATIONS.items():
        parts.append(
            f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{relation["color"]}"/></marker>'
        )
    parts.extend(
        [
            "<style>",
            ".heading{font:700 30px system-ui,-apple-system,sans-serif;fill:#172B4D}",
            ".subheading{font:500 17px system-ui,-apple-system,sans-serif;fill:#5E6C84}",
            ".zone-title{font:700 18px system-ui,-apple-system,sans-serif;fill:#42526E}",
            ".zone-note{font:500 14px system-ui,-apple-system,sans-serif;fill:#6B778C}",
            ".node-title{font:700 18px system-ui,-apple-system,sans-serif;fill:#172B4D}",
            ".node-line{font:500 15px system-ui,-apple-system,sans-serif;fill:#344563}",
            ".edge-label{font:650 12.5px system-ui,-apple-system,sans-serif;fill:#344563}",
            ".badge{font:700 11.5px system-ui,-apple-system,sans-serif;fill:#5E6C84}",
            ".legend{font:600 12.5px system-ui,-apple-system,sans-serif;fill:#344563}",
            "</style>",
            "</defs>",
            f'<rect width="{width}" height="{height}" fill="{COLORS["background"]}"/>',
            _text(36, 46, title, "heading"),
            _text(36, 78, subtitle, "subheading"),
        ]
    )
    for zone in zones:
        parts.extend(_zone(zone))
    parts.append('<g id="connections">')
    for edge in edges:
        parts.append(_edge_path(edge))
    parts.append("</g>")
    parts.append('<g id="zone-labels">')
    for zone in zones:
        parts.extend(_zone_label(zone))
    parts.append("</g>")
    parts.append('<g id="nodes">')
    for node in nodes:
        parts.extend(_node(node))
    parts.append("</g>")
    parts.append('<g id="connection-labels">')
    for edge in edges:
        parts.extend(_edge_label(edge))
    parts.append("</g>")

    legend_x = 40
    legend_y = height - 34
    for relation_name in legend:
        relation = RELATIONS[relation_name]
        dash = f' stroke-dasharray="{relation["dash"]}"' if relation["dash"] else ""
        parts.append(
            f'<line x1="{legend_x}" y1="{legend_y - 5}" x2="{legend_x + 36}" '
            f'y2="{legend_y - 5}" stroke="{relation["color"]}" stroke-width="3"{dash}/>'
        )
        parts.append(_text(legend_x + 46, legend_y, relation["label"], "legend", font_size=12.5))
        legend_x += 70 + _estimated_width(relation["label"], 12.5, bold=True)
    parts.append("</svg>")

    output = Path(script_file).with_name(output_name)
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(output)
    return output
