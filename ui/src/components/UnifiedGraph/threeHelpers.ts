import * as THREE from "three";
import type { GeometryKind } from "./types";

export function makeTextSprite(text: string, highlighted: boolean): THREE.Sprite {
  const padding = 6;
  const fontSize = 22;
  const measure = document.createElement("canvas").getContext("2d")!;
  measure.font = `${fontSize}px system-ui, sans-serif`;
  const textWidth = measure.measureText(text).width;

  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(textWidth + padding * 2);
  canvas.height = fontSize + padding * 2;
  const ctx = canvas.getContext("2d")!;
  ctx.font = `${fontSize}px system-ui, sans-serif`;
  ctx.fillStyle = "rgba(29, 32, 37, 0.85)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = highlighted ? "#ffffff" : "#ece8e1";
  ctx.textBaseline = "top";
  ctx.fillText(text, padding, padding);

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(material);
  const scale = 0.05;
  sprite.scale.set(canvas.width * scale, canvas.height * scale, 1);
  return sprite;
}

export function makeGeometry(kind: GeometryKind | undefined, radius: number): THREE.BufferGeometry {
  switch (kind) {
    case "octahedron": return new THREE.OctahedronGeometry(radius);
    case "icosahedron": return new THREE.IcosahedronGeometry(radius);
    case "box": return new THREE.BoxGeometry(radius * 1.5, radius * 1.5, radius * 1.5);
    case "sphere":
    default: return new THREE.SphereGeometry(radius, 16, 16);
  }
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
