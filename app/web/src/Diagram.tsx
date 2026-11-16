import { createElement, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { RotateCcw, RotateCw, ScanLine } from 'lucide-react';
import type { AssetManifest, VisualTarget } from './api/contracts.generated';
import { copy } from './copy';
import type { Language } from './copy';

const turntable = import.meta.glob('../../../asset_gen/out/relay_4pin_*_??.{jpg,svg}', {
  eager: true, query: '?url', import: 'default',
}) as Record<string, string>;
const frameCount = 24;

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
  const [angle, setAngle] = useState<number>();
  const drag = useRef<{ x: number; angle: number } | null>(null);
  const [overlays, setOverlays] = useState<Record<string, Document>>({});
  const [imageError, setImageError] = useState<string>();
  const [loadedImage, setLoadedImage] = useState<string>();
  const targetView = active && active.asset_id === asset?.asset_id && active.asset_version === asset.asset_version
    && asset.parts.some(part => part.part_id === active.part_id)
    ? asset.views.find(view => view.view_id === active.view_id && (view.frame_id ?? null) === (active.frame_id ?? null)
      && view.visible_parts.includes(active.part_id))
    : undefined;
  const view = targetView ?? asset?.views.find(item => item.view_id === selected) ?? asset?.views[0];
  const frameBase = angle !== undefined && view && asset?.asset_id === 'relay_4pin'
    ? `../../../asset_gen/out/relay_4pin_${view.view_id}_${String(angle).padStart(2, '0')}`
    : undefined;
  const frame = frameBase ? turntable[frameBase + '.jpg'] : undefined;
  const imageSource = frame ?? view?.image;
  const overlaySource = targetView && (frame ? turntable[frameBase + '.svg'] : targetView.overlay);
  const step = (delta: number) => setAngle(previous => ((previous ?? 0) + delta + frameCount) % frameCount);
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

  const cachedOverlay = overlaySource ? overlays[overlaySource] : undefined;
  useEffect(() => {
    if (!frame || !overlaySource || cachedOverlay) return;
    const controller = new AbortController();
    void fetch(overlaySource, { signal: controller.signal }).then(response => {
      if (!response.ok) throw new Error('Frame overlay unavailable');
      return response.text();
    }).then(svg => {
      if (!controller.signal.aborted) {
        const document = new DOMParser().parseFromString(svg, 'image/svg+xml');
        setOverlays(previous => ({ ...previous, [overlaySource]: document }));
      }
    }).catch(() => {});
    return () => controller.abort();
  }, [frame, overlaySource, cachedOverlay]);

  const box = view ? '0 0 ' + view.width + ' ' + view.height : '';
  const selectedPart = active && loadedImage === imageSource
    && cachedOverlay?.documentElement.getAttribute('viewBox') === box
    ? cachedOverlay.getElementById(active.part_id)
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
          onClick={() => { select(item.view_id); setAngle(undefined); }}>{viewName(item.view_id)}</button>)}
      </div>}
    </div>
    <div className="diagram-stage">
      {view && imageError !== view.image ? <div className="diagram-art" style={{ aspectRatio: view.width + '/' + view.height }}
        data-rotatable={true}
        onPointerDown={event => {
          if (event.button !== 0) return;
          drag.current = { x: event.clientX, angle: angle ?? 0 };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={event => {
          if (!drag.current) return;
          const index = drag.current.angle + Math.round((event.clientX - drag.current.x) / 12);
          setAngle(((index % frameCount) + frameCount) % frameCount);
        }}
        onLostPointerCapture={() => { drag.current = null; }}>
        <img key={imageSource} src={imageSource} draggable={false} alt={t.shortTitle + ' · ' + viewName(view.view_id)}
          onLoad={() => setLoadedImage(imageSource)}
          onError={() => frame ? setAngle(undefined) : setImageError(view.image)} />
        {selectedPart && <svg className="part-overlay" viewBox={box} aria-hidden="true">{outline(selectedPart, 0)}</svg>}
      </div> : <div className="diagram-empty"><ScanLine size={30} aria-hidden="true" />{t.diagramUnavailable}</div>}
      {part && view && imageError !== view.image &&
        <span className="part-label" role="status"><span />{language === 'ur' ? part.label_ur : part.label_en}</span>}
    </div>
    {view && <div className="rotation-controls">
      <button type="button" className="button secondary"
        aria-label={language === 'ur' ? 'بائیں گھمائیں' : 'Rotate left'} onClick={() => step(-1)}><RotateCcw size={17} /></button>
      <span>{language === 'ur' ? 'گھمانے کے لیے کھینچیں' : 'Drag to rotate'}</span>
      <button type="button" className="button secondary"
        aria-label={language === 'ur' ? 'دائیں گھمائیں' : 'Rotate right'} onClick={() => step(1)}><RotateCw size={17} /></button>
    </div>}
  </section>;
}
