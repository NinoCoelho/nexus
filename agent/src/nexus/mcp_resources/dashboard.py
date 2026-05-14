"""Generate self-contained HTML for a dashboard chart widget."""

from __future__ import annotations

import html as html_mod
import json
import logging

log = logging.getLogger(__name__)

_CHART_JS = r"""
<script>
// Minimal Chart.js subset — bar, line, area, pie, donut, kpi
(function(){
  function el(tag,cls,style){var e=document.createElement(tag);if(cls)e.className=cls;if(style)e.style.cssText=style;return e}
  function drawBar(cx,cy,cw,ch,cols,rows,viz){var n=cols.length>0?cols.length:1,bw=Math.min(40,cw/n*.6),max=0;rows.forEach(function(r){cols.forEach(function(c){var v=parseFloat(r[c])||0;if(v>max)max=v})});max=max||1;var colors=['#60a5fa','#34d399','#f97316','#a78bfa','#f472b6','#fbbf24'];cols.forEach(function(col,i){rows.forEach(function(row,j){var v=parseFloat(row[col])||0;var h=v/max*(ch-30);var x=10+i*(cw/n)+j*(bw/rows.length);cx.fillStyle=colors[j%colors.length];cx.fillRect(x,ch-h-20,bw-2,h);cx.fillStyle='#94a3b8';cx.font='10px sans-serif';cx.textAlign='center';cx.fillText(row[cols[0]]||'',x+bw/2,ch-6);cx.fillText(v.toFixed(0),x+bw/2,ch-h-24)})})}
  function drawLine(cx,cy,cw,ch,cols,rows,type){if(rows.length<2)return;var max=0,pad=30;cols.forEach(function(c){rows.forEach(function(r){var v=parseFloat(r[c])||0;if(v>max)max=v})});max=max||1;var colors=['#60a5fa','#34d399','#f97316','#a78bfa','#f472b6'];var stepX=(cw-pad*2)/(rows.length-1||1);cols.forEach(function(col,ci){cx.beginPath();var fill=type==='area';cx.strokeStyle=colors[ci%colors.length];cx.fillStyle=colors[ci%colors.length]+'33';rows.forEach(function(row,i){var x=pad+i*stepX;var v=parseFloat(row[col])||0;var y=ch-pad-v/max*(ch-pad*2);if(i===0)cx.moveTo(x,y);else cx.lineTo(x,y)});cx.stroke();if(fill){cx.lineTo(pad+(rows.length-1)*stepX,ch-pad);cx.lineTo(pad,ch-pad);cx.closePath();cx.fill()}})}
  function drawPie(cx,cy,cw,ch,rows,viz){var total=0;rows.forEach(function(r){total+=parseFloat(r[viz.y_field]||r[Object.keys(r)[1]])||0});total=total||1;var r=Math.min(cw,ch)/2-30,cx0=cw/2,cy0=ch/2,angle=-Math.PI/2;var colors=['#60a5fa','#34d399','#f97316','#a78bfa','#f472b6','#fbbf24','#f87171','#38bdf8'];rows.forEach(function(row,i){var v=parseFloat(row[viz.y_field]||row[Object.keys(row)[1]])||0;var a=v/total*Math.PI*2;cx.beginPath();cx.moveTo(cx0,cy0);cx.arc(cx0,cy0,r,angle,angle+a);cx.fillStyle=colors[i%colors.length];cx.fill();cx.strokeStyle='#1a1a2e';cx.lineWidth=2;cx.stroke();var mid=angle+a/2;var lx=cx0+Math.cos(mid)*(r+14),ly=cy0+Math.sin(mid)*(r+14);cx.fillStyle='#e0e0e0';cx.font='10px sans-serif';cx.textAlign=Math.cos(mid)>0?'left':'right';cx.fillText((row[Object.keys(row)[0]]||'')+' '+v.toFixed(0),lx,ly);angle+=a})}
  window.addEventListener('DOMContentLoaded',function(){
    var dataEl=document.getElementById('chart-data');
    if(!dataEl)return;
    var d=JSON.parse(dataEl.textContent);
    var canvas=document.getElementById('chart-canvas');
    if(!canvas)return;
    var cx=canvas.getContext('2d');
    var w=canvas.parentElement.clientWidth||300,h=220;
    canvas.width=w;canvas.height=h;
    cx.clearRect(0,0,w,h);
    var viz=d.viz_type,rows=d.rows||[],cols=d.columns||[];
    if(viz==='kpi'){canvas.style.display='none';var ke=document.getElementById('kpi-display');if(ke){var val=rows.length>0?parseFloat(rows[0][cols.length>1?cols[1]:cols[0]])||0:0;ke.textContent=val.toLocaleString();ke.style.display='block'}}
    else if(viz==='pie'||viz==='donut'){drawPie(cx,0,w,h,rows,d.viz_config||{});if(viz==='donut'){cx.beginPath();cx.arc(w/2,h/2,Math.min(w,h)/2-30-20,0,Math.PI*2);cx.fillStyle='#1a1a2e';cx.fill()}}
    else if(viz==='line'||viz==='area'){drawLine(cx,0,w,h,cols.slice(1),rows,viz)}
    else{drawBar(cx,0,w,h,cols.slice(1),rows,d.viz_config||{})}
  });
})();
</script>
"""


def render_widget(folder: str, widget_id: str) -> str:
    from ..vault_dashboard import read_dashboard
    from ..vault_widgets import read_widget_result
    from . import _wrap

    if not folder or not widget_id:
        return _wrap("<p style='color:#ef4444'>Missing folder or widget_id parameter</p>")

    try:
        dashboard = read_dashboard(folder)
    except Exception as e:
        log.exception("dashboard render failed for folder=%r", folder)
        return _wrap(f"<p style='color:#ef4444'>Error loading dashboard: {html_mod.escape(str(e))}</p>")

    widget = None
    for w in dashboard.get("widgets", []):
        if w.get("id") == widget_id:
            widget = w
            break

    if widget is None:
        return _wrap(f"<p style='color:#ef4444'>Widget {html_mod.escape(widget_id)} not found in dashboard</p>")

    result_json = read_widget_result(folder, widget_id)
    if not result_json:
        return _wrap(
            f"<p style='color:#94a3b8'>Widget <b>{html_mod.escape(widget.get('title', widget_id))}</b> has not been executed yet.</p>"
        )

    try:
        result = json.loads(result_json)
    except json.JSONDecodeError:
        return _wrap(f"<p style='color:#ef4444'>Invalid widget result data</p>")

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    viz_type = widget.get("viz_type", "bar")
    viz_config = widget.get("viz_config", {})
    widget_title = html_mod.escape(widget.get("title", widget_id))

    col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]

    chart_data = json.dumps({
        "viz_type": viz_type,
        "viz_config": viz_config,
        "columns": col_names,
        "rows": rows,
    })

    chart_area = (
        f'<div style="position:relative"><canvas id="chart-canvas" style="width:100%;height:220px"></canvas>'
        f'<div id="kpi-display" style="display:none;text-align:center;font-size:36px;font-weight:700;color:#60a5fa;padding:60px 0"></div></div>'
    )

    body = (
        "<style>.nx-widget{background:#0f172a;border-radius:8px;padding:12px}"
        ".nx-widget-title{font-size:13px;font-weight:600;margin-bottom:8px;color:#e0e0e0}</style>"
        f'<div class="nx-widget">'
        f'<div class="nx-widget-title">{widget_title}</div>'
        f'{chart_area}'
        f'</div>'
        f'<script id="chart-data" type="application/json">{html_mod.escape(chart_data)}</script>'
        f'{_CHART_JS}'
    )
    return _wrap(body)
