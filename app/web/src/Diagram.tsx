import { createElement, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { ScanLine } from 'lucide-react';
import type { AssetManifest, VisualTarget } from './api/contracts.generated';
import { copy } from './copy';
import type { Language } from './copy';

const geometry: Record<string, string[]> = {
  g: ['transform'],
  path: ['d', 'transform', 'fillRule'],
  polygon: ['points', 'transform'],
  polyline: ['points', 'transform'],
  rect: ['x', 'y', 'width', 'height', 'rx', 'ry', 'transform'],
  circle: ['cx', 'cy', 'r', 'transform'],
  ellipse: ['cx', 'cy', 'rx', 'ry', 'transform'],
};

function outline(element: Element, key: number): ReactNode {
  const tag = element.localName;
  const allowed = Object.hasOwn(geometry, tag) ? geometry[tag] : undefined;
  if (!allowed) return null;
  const props: Record<string, string | number> = { key };
  for (const name of allowed) {
    const value = element.getAttribute(name === 'fillRule' ? 'fill-rule' : name);
    if (value !== null) props[name] = value;
  }
  return createElement(tag, props, tag === 'g' ? Array.from(element.children, outline) : undefined);
}

export function Diagram({ asset, active, language }: {
  asset?: AssetManifest; active?: VisualTarget | null; language: Language;
}) {
  const t = copy[language];
  const [selected, select] = useState('exterior');
  const [overlays, setOverlays] = useState<Record<string, Document>>({});
  const [imageError, setImageError] = useState<string>();
  const targetView = active && active.asset_id === asset?.asset_id && active.asset_version === asset.asset_version
    && asset.parts.some(part => part.part_id === active.part_id)
    ? asset.views.find(view => view.view_id === active.view_id && (view.frame_id ?? null) === (active.frame_id ?? null)
      && view.visible_parts.includes(active.part_id))
    : undefined;
  const view = targetView ?? asset?.views.find(item => item.view_id === selected) ?? asset?.views[0];
  useEffect(() => {
    if (!asset) return;
    const controller = new AbortController();
    for (const item of asset.views) {
      const image = new Image();
      image.src = item.image;
      fetch(item.overlay, { signal: controller.signal }).then(response => {
        if (!response.ok) throw new Error('Overlay unavailable');
        return response.text();
      }).then(svg => {
        if (!controller.signal.aborted) {
          const document = new DOMParser().parseFromString(svg, 'image/svg+xml');
          setOverlays(previous => ({ ...previous, [item.overlay]: document }));
        }
      }).catch(() => {});
    }
    return () => controller.abort();
  }, [asset]);

  useEffect(() => {
    if (targetView) select(targetView.view_id);
  }, [targetView]);

  const box = view ? '0 0 ' + view.width + ' ' + view.height : '';
  const overlay = targetView ? overlays[targetView.overlay] : undefined;
  const selectedPart = active && overlay?.documentElement.getAttribute('viewBox') === box
    ? overlay.getElementById(active.part_id)
    : null;
  const part = selectedPart ? asset?.parts.find(item => item.part_id === active?.part_id) : undefined;
  const viewName = (name: string) => name === 'cutaway' ? t.cutaway : t.exterior;

  return <section className="panel diagram" aria-labelledby="diagram-title">
    <div className="panel-heading">
      <h2 id="diagram-title"><ScanLine size={18} aria-hidden="true" />{t.visual}</h2>
      {asset && <div className="view-switch" role="group" aria-label={t.visual}>
        {asset.views.map(item => <button type="button" key={item.view_id}
          aria-pressed={view?.view_id === item.view_id}
          disabled={!!targetView}
          onClick={() => select(item.view_id)}>{viewName(item.view_id)}</button>)}
      </div>}
    </div>
    <div className="diagram-stage">
      {view && imageError !== view.image ? <div className="diagram-art" style={{ aspectRatio: view.width + '/' + view.height }}>
        <img src={view.image} alt={t.shortTitle + ' · ' + viewName(view.view_id)} onError={() => setImageError(view.image)} />
        {selectedPart && <svg className="part-overlay" viewBox={box} aria-hidden="true">{outline(selectedPart, 0)}</svg>}
      </div> : <div className="diagram-empty"><ScanLine size={30} aria-hidden="true" />{t.diagramUnavailable}</div>}
      {part && view && imageError !== view.image &&
        <span className="part-label" role="status"><span />{language === 'ur' ? part.label_ur : part.label_en}</span>}
    </div>
  </section>;
}
