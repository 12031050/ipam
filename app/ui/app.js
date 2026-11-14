/* IPAM fnOS 版前端：原生 JS 单页应用，无外部依赖。 */
'use strict';

const S = {
  user: null, appName: 'IP 地址管理', branding: {}, fieldLabels: {},
  statusLabels: {}, assetStatusLabels: {}, options: {}, session: {},
  view: 'dashboard', page: 1, perPage: 30, sort: '', order: 'desc', q: '',
  filters: {}, selected: new Set(), pageIds: [], importData: null, sysStats: null,
};

const $ = (s, root) => (root || document).querySelector(s);
const $$ = (s, root) => Array.from((root || document).querySelectorAll(s));
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const attr = esc;
const fmt = (n) => Number(n || 0).toLocaleString('zh-CN');
const SOURCE_DEFAULT = 'https://github.com/12031050/ipam.git';
const isHttpUrl = (v) => /^https?:\/\/[^\s<>"']+$/i.test(String(v == null ? '' : v).trim());
/* 仅 http/https 协议渲染为链接，避免用户自填值引入 javascript: 等危险协议 */
const linkHtml = (v) => {
  const u = String(v == null ? '' : v).trim();
  return isHttpUrl(u)
    ? '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(u) + '</a>'
    : esc(u);
};

async function api(method, path, body) {
  const opt = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch('api/' + path.replace(/^\//, ''), opt);
  let out;
  try { out = await res.json(); } catch (e) { throw new Error('服务器响应格式错误'); }
  if (res.status === 401 && path !== 'login') { showLogin(); throw new Error('请先登录'); }
  if (!out.ok) throw new Error(out.error || '请求失败');
  return out.data;
}

function toast(msg, kind) {
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || 'ok');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

function modal(title, body, footer) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = body;
  $('#modalFoot').innerHTML = footer || '<button class="btn" data-close>关闭</button>';
  $('#modalOverlay').hidden = false;
  $$('[data-close]', $('#modalFoot')).forEach(b => b.onclick = closeModal);
}
function closeModal() { $('#modalOverlay').hidden = true; }
function confirmBox(msg, onYes) {
  modal('请确认', '<p style="margin:0;line-height:1.8">' + esc(msg) + '</p>',
    '<button class="btn" data-close>取消</button><button class="btn btn-danger" id="confirmYes">确定</button>');
  $('#confirmYes').onclick = async () => { closeModal(); try { await onYes(); } catch (e) { toast(e.message, 'err'); } };
}

function opts(list, value, placeholder) {
  let html = placeholder === undefined ? '' : '<option value="">' + esc(placeholder) + '</option>';
  (list || []).forEach(o => {
    const v = typeof o === 'object' ? o.value : o;
    const l = typeof o === 'object' ? o.label : o;
    html += '<option value="' + attr(v) + '"' + (String(v) === String(value == null ? '' : value) ? ' selected' : '') +
      '>' + esc(l) + '</option>';
  });
  return html;
}

function formHtml(fields, values) {
  values = values || {};
  let html = '<div class="form-grid">';
  fields.forEach(f => {
    const v = values[f.k] == null ? (f.def == null ? '' : f.def) : values[f.k];
    const id = 'f_' + f.k;
    const req = f.required ? ' required' : '';
    let ctrl;
    if (f.type === 'select') {
      const list = typeof f.options === 'function' ? f.options() : (f.options || []);
      ctrl = '<select id="' + id + '" name="' + f.k + '"' + req + '>' + opts(list, v, f.placeholder) + '</select>';
    } else if (f.type === 'textarea') {
      ctrl = '<textarea id="' + id + '" name="' + f.k + '" rows="3">' + esc(v) + '</textarea>';
    } else if (f.type === 'checkbox') {
      ctrl = '<select id="' + id + '" name="' + f.k + '"><option value="1"' + (v ? ' selected' : '') +
        '>启用</option><option value="0"' + (!v ? ' selected' : '') + '>停用</option></select>';
    } else if (f.type === 'tags') {
      ctrl = '<select id="' + id + '" name="' + f.k + '" multiple size="4">' +
        (S.options.tags || []).map(t => '<option value="' + attr(t.id) + '"' +
          ((values.tagIds || []).includes(t.id) ? ' selected' : '') + '>' + esc(t.name) + '</option>').join('') + '</select>';
    } else {
      const t = f.type === 'number' ? 'number' : (f.type === 'password' ? 'password' : (f.type === 'color' ? 'color' : 'text'));
      ctrl = '<input id="' + id + '" name="' + f.k + '" type="' + t + '" value="' + attr(v) + '"' + req +
        (f.placeholder ? ' placeholder="' + attr(f.placeholder) + '"' : '') + '>';
    }
    html += '<div class="' + (f.full ? 'full' : '') + '"><label for="' + id + '">' + esc(f.t) +
      (f.required ? ' *' : '') + '</label>' + ctrl + '</div>';
  });
  html += '</div>';
  if (fields.some(f => f.note)) html += '<p class="form-note">' + esc(fields.find(f => f.note).note) + '</p>';
  return html;
}

function readForm() {
  const out = {};
  $$('#modalBody [name]').forEach(el => {
    if (el.multiple) out[el.name] = Array.from(el.selectedOptions).map(o => o.value);
    else out[el.name] = el.value;
  });
  return out;
}

/* ---------------- 启动与登录 ---------------- */

function showLogin(foot) {
  $('#app').hidden = true;
  $('#loginScreen').hidden = false;
  $('#loginFoot').textContent = foot || '';
  const b = S.branding || {};
  const meta = [b.copyright || '© 2026 梦非常浪漫',
    b.contact_email || '12031050@qq.com', b.contact || 'QQ群：416290431'].join(' · ');
  const src = (b.source_url || SOURCE_DEFAULT).trim();
  $('#loginCopyright').innerHTML = esc(meta) +
    (src ? '<br>开源地址：' + linkHtml(src) : '');
  $('#loginError').hidden = true;
  $('#loginPass').value = '';
}
function showApp() {
  $('#loginScreen').hidden = true;
  $('#app').hidden = false;
}

async function boot() {
  const theme = localStorage.getItem('ipam_theme') || 'dark';
  document.documentElement.setAttribute('data-theme', theme);
  try {
    const session = await api('GET', 'session');
    onSession(session);
    await loadOptions();
    showApp();
    renderNav();
    go(location.hash.replace('#', '') || 'dashboard');
  } catch (e) {
    let foot = '';
    try {
      const p = await api('GET', 'ping');
      S.branding = p.branding || {};
      $('#loginTitle').textContent = p.branding.app_name || 'IP 地址管理';
      document.title = $('#loginTitle').textContent;
      if (!p.initialized) foot = '尚未初始化管理员，请在飞牛应用中心重新安装本应用。';
    } catch (err) { /* ignore */ }
    showLogin(foot);
  }
}

function onSession(s) {
  S.session = s; S.user = s.user;
  S.appName = s.app_name; S.branding = s.branding || {};
  S.fieldLabels = s.field_labels || {};
  S.statusLabels = s.status_labels || {};
  S.assetStatusLabels = s.asset_status_labels || {};
  document.title = S.appName;
  $('#brandName').textContent = S.appName;
  $('#loginTitle').textContent = S.appName;
  const label = (s.user.role === 'admin' ? '管理员' : '普通用户');
  $('#sideUser').textContent = s.user.username + ' · ' + label;
  $('#topUser').textContent = s.user.username;
  $('#btnLogout').style.display = s.user.via_gateway ? 'none' : '';
}

async function loadOptions() {
  try { S.options = await api('GET', 'filter_options'); } catch (e) { S.options = {}; }
}

$('#loginForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  $('#loginError').hidden = true;
  try {
    await api('POST', 'login', { username: $('#loginUser').value.trim(), password: $('#loginPass').value });
    const s = await api('GET', 'session');
    onSession(s); await loadOptions(); showApp(); renderNav(); go('dashboard');
    toast('登录成功');
  } catch (e) {
    $('#loginError').textContent = e.message;
    $('#loginError').hidden = false;
  }
});

$('#btnLogout').onclick = async () => {
  try { await api('POST', 'logout', {}); } catch (e) { /* ignore */ }
  showLogin();
};
$('#btnTheme').onclick = () => {
  const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('ipam_theme', next);
};
$('#menuToggle').onclick = () => $('#sidebar').classList.toggle('open');
$('#modalClose').onclick = closeModal;
/* 禁止点击遮罩关闭：新增/编辑表单较长，误点对话框外会直接丢失已填内容。
   关闭只能通过右上角 × 或底部「取消 / 关闭」按钮。 */
$('#globalSearch').addEventListener('keydown', e => {
  if (e.key === 'Enter') { S.q = e.target.value.trim(); S.page = 1; go('records'); }
});

/* ---------------- 导航 ---------------- */

const NAV = [
  ['概览', [['dashboard', '仪表盘', '◈'], ['statistics', '统计分析', '◫']]],
  ['地址管理', [['records', 'IP 记录', '▤'], ['subnets', '子网', '◇'], ['vlans', 'VLAN', '⌗'],
    ['ip_pools', 'IP 地址池', '▦'], ['reserved_ips', '保留地址', '⚑'], ['address_spaces', '地址空间', '⬡']]],
  ['资产与组织', [['assets', '资产设备', '▣'], ['organizations', '单位', '◎'], ['departments', '部门', '⊞'],
    ['location_nodes', '物理位置', '⌖'], ['tags', '标签', '◆']]],
  ['系统', [['import_export', '导入导出', '⇅'], ['backup', '备份恢复', '⛁'], ['audit', '审计日志', '≡'],
    ['users', '用户管理', '☰'], ['saved_views', '保存视图', '★'], ['settings', '系统设置', '⚙'], ['about', '关于', 'ⓘ']]],
];

function renderNav() {
  let html = '';
  NAV.forEach(([group, items]) => {
    html += '<div class="nav-group">' + esc(group) + '</div>';
    items.forEach(([id, label, ico]) => {
      if (S.user.role !== 'admin' && ['users', 'backup', 'audit', 'settings'].includes(id) && id !== 'settings') return;
      html += '<button class="nav-item' + (S.view === id ? ' active' : '') + '" data-view="' + id + '">' +
        '<span class="nav-ico">' + ico + '</span>' + esc(label) + '</button>';
    });
  });
  $('#nav').innerHTML = html;
  $$('#nav .nav-item').forEach(b => b.onclick = () => {
    $('#sidebar').classList.remove('open');
    go(b.dataset.view);
  });
}

function go(view) {
  S.view = view; S.page = 1; S.q = ''; S.filters = {}; S.selected.clear(); S.sort = '';
  location.hash = view;
  $$('#nav .nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  let title = view;
  NAV.forEach(g => g[1].forEach(i => { if (i[0] === view) title = i[1]; }));
  $('#pageTitle').textContent = title;
  $('#globalSearch').value = '';
  const fn = SPECIAL[view];
  if (fn) fn(); else renderCrud(view);
}

/* ---------------- 资源配置 ---------------- */

const statusOpts = () => Object.entries(S.statusLabels).map(([value, label]) => ({ value, label }));
const assetStatusOpts = () => Object.entries(S.assetStatusLabels).map(([value, label]) => ({ value, label }));
const orgOpts = () => (S.options.organizations || []).map(o => ({ value: o.id, label: o.name }));
const deptOpts = () => (S.options.departments || []).map(o => ({ value: o.id, label: o.name + (o.org_id ? '' : '') }));
const subnetOpts = () => (S.options.subnets || []).map(o => ({ value: o.id, label: o.cidr + (o.name ? ' · ' + o.name : '') }));
const vlanOpts = () => (S.options.vlans || []).map(o => ({ value: o.id, label: 'VLAN ' + o.vlan_id + (o.name ? ' · ' + o.name : '') }));
const tagOpts = () => (S.options.tags || []).map(o => ({ value: o.id, label: o.name }));

const RES = {
  records: {
    endpoint: 'records', name: 'IP 记录',
    cols: [
      { k: 'ip_address', t: 'IP 地址', sort: true, render: r => '<span class="mono">' + esc(r.endpoint_display || r.ip_address) + '</span>' },
      { k: 'status', t: '状态', render: r => '<span class="badge ' + attr(r.status) + '">' + esc(r.status_text) + '</span>' },
      { k: 'device_name', t: '设备名称', sort: true }, { k: 'device_type', t: '类型' },
      { k: 'mac_address', t: 'MAC', render: r => '<span class="mono">' + esc(r.mac_address) + '</span>' },
      { k: 'owner', t: '使用人', sort: true }, { k: 'org_name', t: '单位' }, { k: 'dept_name', t: '部门' },
      { k: 'subnet_cidr', t: '子网', render: r => '<span class="mono">' + esc(r.subnet_cidr) + '</span>' },
      { k: 'tag_names', t: '标签' },
      { k: 'online_text', t: '在线', render: r => onlineBadge(r) },
      { k: 'updated_at', t: '更新时间', sort: true },
    ],
    filters: [
      { k: 'status', t: '状态', options: statusOpts }, { k: 'org_id', t: '单位', options: orgOpts },
      { k: 'department_id', t: '部门', options: deptOpts }, { k: 'subnet_id', t: '子网', options: subnetOpts },
      { k: 'tag_id', t: '标签', options: tagOpts },
    ],
    fields: () => [
      { k: 'ip_address', t: 'IP 地址', required: true, placeholder: '192.168.1.10' },
      { k: 'port', t: '端口', type: 'number', def: 0 },
      { k: 'status', t: '状态', type: 'select', options: statusOpts, def: 'in_use' },
      { k: 'device_name', t: '设备名称' }, { k: 'device_type', t: '设备类型' },
      { k: 'mac_address', t: 'MAC 地址', placeholder: 'AA:BB:CC:DD:EE:FF' },
      { k: 'owner', t: '使用人' }, { k: 'business_system', t: '业务系统' },
      { k: 'org_id', t: '单位', type: 'select', options: [].concat(orgOpts(), []), placeholder: '未分配' },
      { k: 'department_id', t: '部门', type: 'select', options: deptOpts, placeholder: '未分配' },
      { k: 'subnet_id', t: '子网', type: 'select', options: subnetOpts, placeholder: '自动推断' },
      { k: 'tags', t: '标签', type: 'tags', full: true },
      { k: 'remark', t: '备注', type: 'textarea', full: true },
    ],
    batch: true,
  },
  subnets: {
    endpoint: 'subnets', name: '子网',
    cols: [
      { k: 'cidr', t: 'CIDR', sort: true, render: r => '<span class="mono">' + esc(r.cidr) + '</span>' },
      { k: 'name', t: '名称' }, { k: 'vlan_number', t: 'VLAN' },
      { k: 'gateway', t: '网关', render: r => '<span class="mono">' + esc(r.gateway) + '</span>' },
      { k: 'network', t: '网络地址' }, { k: 'broadcast', t: '广播地址' },
      { k: 'total', t: '可用地址', render: r => fmt(r.total) },
      { k: 'used', t: '已用' },
      { k: 'utilization', t: '利用率', render: r => '<div class="progress" style="width:110px"><i style="width:' + Number(r.utilization) + '%"></i></div><span class="small dim">' + esc(r.utilization) + '%</span>' },
      { k: 'description', t: '说明' },
    ],
    fields: () => [
      { k: 'cidr', t: 'CIDR', required: true, placeholder: '192.168.1.0/24' },
      { k: 'name', t: '名称' },
      { k: 'vlan_id', t: 'VLAN', type: 'select', options: vlanOpts, placeholder: '未绑定' },
      { k: 'gateway', t: '网关' },
      { k: 'description', t: '说明', type: 'textarea', full: true,
        note: '创建时会校验 CIDR 合法性并检测与现有网段是否重叠。' },
    ],
  },
  vlans: {
    endpoint: 'vlans', name: 'VLAN',
    cols: [{ k: 'vlan_id', t: 'VLAN ID', sort: true }, { k: 'name', t: '名称' },
      { k: 'purpose', t: '用途' }, { k: 'subnet_count', t: '子网数' }, { k: 'description', t: '说明' }],
    fields: () => [{ k: 'vlan_id', t: 'VLAN ID (1-4094)', type: 'number', required: true },
      { k: 'name', t: '名称' }, { k: 'purpose', t: '用途' },
      { k: 'description', t: '说明', type: 'textarea', full: true }],
  },
  ip_pools: {
    endpoint: 'ip_pools', name: 'IP 地址池',
    cols: [{ k: 'name', t: '名称' },
      { k: 'range_start', t: '起始', render: r => '<span class="mono">' + esc(r.range_start) + '</span>' },
      { k: 'range_end', t: '结束', render: r => '<span class="mono">' + esc(r.range_end) + '</span>' },
      { k: 'subnet_cidr', t: '子网' }, { k: 'total', t: '总数' },
      { k: 'allocated', t: '已分配' }, { k: 'available', t: '可用' }, { k: 'description', t: '说明' }],
    fields: () => [{ k: 'name', t: '名称', required: true },
      { k: 'range_start', t: '起始 IP', required: true }, { k: 'range_end', t: '结束 IP', required: true },
      { k: 'subnet_id', t: '所属子网', type: 'select', options: subnetOpts, placeholder: '未指定' },
      { k: 'description', t: '说明', type: 'textarea', full: true, note: '地址池最多支持 65536 个地址。' }],
    rowActions: [
      { t: '分配', fn: r => allocatePool(r) },
      { t: '回收', fn: r => releasePool(r) },
    ],
  },
  reserved_ips: {
    endpoint: 'reserved_ips', name: '保留地址',
    cols: [{ k: 'ip_address', t: 'IP 地址', render: r => '<span class="mono">' + esc(r.ip_address) + '</span>' },
      { k: 'subnet_cidr', t: '子网' }, { k: 'purpose', t: '用途' }, { k: 'remark', t: '备注' }],
    fields: () => [{ k: 'ip_address', t: 'IP 地址', required: true },
      { k: 'subnet_id', t: '子网', type: 'select', options: subnetOpts, placeholder: '未指定' },
      { k: 'purpose', t: '用途' }, { k: 'remark', t: '备注', type: 'textarea', full: true }],
  },
  address_spaces: {
    endpoint: 'address_spaces', name: '地址空间',
    cols: [{ k: 'name', t: '名称' }, { k: 'cidr', t: 'CIDR', render: r => '<span class="mono">' + esc(r.cidr) + '</span>' },
      { k: 'total', t: '地址数', render: r => fmt(r.total) }, { k: 'subnet_count', t: '包含子网' }, { k: 'description', t: '说明' }],
    fields: () => [{ k: 'name', t: '名称', required: true }, { k: 'cidr', t: 'CIDR', required: true },
      { k: 'description', t: '说明', type: 'textarea', full: true }],
  },
  assets: {
    endpoint: 'assets', name: '资产',
    cols: [{ k: 'asset_number', t: '资产编号', sort: true }, { k: 'asset_name', t: '资产名称', sort: true },
      { k: 'asset_type', t: '类型' }, { k: 'brand', t: '品牌' }, { k: 'model', t: '型号' },
      { k: 'status', t: '状态', render: r => '<span class="badge ' + attr(r.status) + '">' + esc(r.status_text) + '</span>' },
      { k: 'location', t: '位置' }, { k: 'owner', t: '负责人' }, { k: 'serial_number', t: '序列号' },
      { k: 'binding_count', t: '绑定 IP' }],
    filters: [{ k: 'status', t: '状态', options: assetStatusOpts },
      { k: 'asset_type', t: '类型', options: () => (S.options.asset_types || []).map(x => ({ value: x, label: x })) }],
    fields: () => [
      { k: 'asset_number', t: '资产编号', required: true }, { k: 'asset_name', t: '资产名称', required: true },
      { k: 'asset_type', t: '资产类型' }, { k: 'brand', t: '品牌' }, { k: 'model', t: '型号' },
      { k: 'serial_number', t: '序列号' },
      { k: 'status', t: '状态', type: 'select', options: assetStatusOpts, def: 'in_use' },
      { k: 'location', t: '位置' }, { k: 'owner', t: '负责人' },
      { k: 'purchase_date', t: '购买日期', placeholder: 'YYYY-MM-DD' },
      { k: 'warranty_expire_at', t: '保修到期', placeholder: 'YYYY-MM-DD' },
      { k: 'remark', t: '备注', type: 'textarea', full: true }],
    rowActions: [{ t: '绑定 IP', fn: r => assetBindings(r) }],
  },
  organizations: {
    endpoint: 'organizations', name: '单位',
    cols: [{ k: 'name', t: '名称', sort: true }, { k: 'description', t: '说明' },
      { k: 'dept_count', t: '部门数' }, { k: 'record_count', t: 'IP 记录数' }],
    fields: () => [{ k: 'name', t: '名称', required: true },
      { k: 'description', t: '说明', type: 'textarea', full: true }],
    adminOnly: true,
  },
  departments: {
    endpoint: 'departments', name: '部门',
    cols: [{ k: 'name', t: '名称', sort: true }, { k: 'org_name', t: '所属单位' }, { k: 'record_count', t: 'IP 记录数' }],
    fields: () => [{ k: 'name', t: '名称', required: true },
      { k: 'org_id', t: '所属单位', type: 'select', options: orgOpts, placeholder: '未分配' }],
  },
  location_nodes: {
    endpoint: 'location_nodes', name: '物理位置',
    cols: [{ k: 'name', t: '名称', sort: true }, { k: 'node_type_label', t: '类型' },
      { k: 'parent_name', t: '上级' }, { k: 'child_count', t: '子节点' }, { k: 'description', t: '说明' }],
    fields: () => [{ k: 'name', t: '名称', required: true },
      { k: 'node_type', t: '类型', type: 'select', options: [
        { value: 'campus', label: '园区' }, { value: 'building', label: '楼栋' },
        { value: 'floor', label: '楼层' }, { value: 'room', label: '机房' }, { value: 'cabinet', label: '机柜' }], def: 'room' },
      { k: 'parent_id', t: '上级节点', type: 'select',
        options: () => (S.options.locations || []).map(o => ({ value: o.id, label: o.name })), placeholder: '顶级' },
      { k: 'description', t: '说明', type: 'textarea', full: true }],
  },
  tags: {
    endpoint: 'tags', name: '标签',
    cols: [{ k: 'name', t: '名称', sort: true },
      { k: 'color', t: '颜色', render: r => '<span style="display:inline-block;width:14px;height:14px;border-radius:4px;background:' +
        attr(r.color) + ';vertical-align:middle"></span> <span class="mono">' + esc(r.color) + '</span>' },
      { k: 'record_count', t: '关联记录' }],
    fields: () => [{ k: 'name', t: '名称', required: true }, { k: 'color', t: '颜色', type: 'color', def: '#38bdf8' }],
  },
  users: {
    endpoint: 'users', name: '用户',
    cols: [{ k: 'username', t: '账号', sort: true },
      { k: 'role', t: '角色', render: r => '<span class="badge ' + (r.role === 'admin' ? 'in_use' : '') + '">' + (r.role === 'admin' ? '管理员' : '普通用户') + '</span>' },
      { k: 'org_name', t: '数据范围' },
      { k: 'is_active', t: '状态', render: r => r.is_active ? '<span class="badge in_use">启用</span>' : '<span class="badge disabled">停用</span>' },
      { k: 'last_login', t: '最后登录' }, { k: 'created_at', t: '创建时间' }],
    fields: () => [{ k: 'username', t: '账号', required: true },
      { k: 'password', t: '密码', type: 'password', placeholder: '留空表示不修改，至少 6 位' },
      { k: 'role', t: '角色', type: 'select', options: [{ value: 'user', label: '普通用户' }, { value: 'admin', label: '管理员' }], def: 'user' },
      { k: 'org_id', t: '限定单位', type: 'select', options: orgOpts, placeholder: '可见全部数据' },
      { k: 'is_active', t: '状态', type: 'checkbox', def: 1 }],
    adminOnly: true,
  },
  saved_views: {
    endpoint: 'saved_views', name: '保存视图',
    cols: [{ k: 'name', t: '名称' },
      { k: 'scope', t: '范围', render: r => r.scope === 'public' ? '公共视图' : '个人视图' },
      { k: 'owner', t: '创建人' }, { k: 'created_at', t: '创建时间' }],
    fields: () => [{ k: 'name', t: '名称', required: true },
      { k: 'scope', t: '范围', type: 'select', options: [{ value: 'personal', label: '个人视图' }, { value: 'public', label: '公共视图' }], def: 'personal' },
      { k: 'filters_json', t: '筛选条件 (JSON)', type: 'textarea', full: true, def: '{}' }],
  },
};

/* ---------------- 通用 CRUD 视图 ---------------- */

function queryString(cfg) {
  const p = new URLSearchParams();
  p.set('page', S.page); p.set('per_page', S.perPage);
  if (S.q) p.set('q', S.q);
  if (S.sort) { p.set('sort', S.sort); p.set('order', S.order); }
  Object.entries(S.filters).forEach(([k, v]) => { if (v) p.set(k, v); });
  return p.toString();
}

function cell(r, c) {
  if (c.render) return c.render(r);
  const v = r[c.k];
  return esc(v == null ? '' : v);
}

/* 从未执行过在线探测的记录显示「未探测」，避免与真正探测出离线相混淆；
   鼠标悬停可看到最近一次探测时间。 */
function onlineBadge(r) {
  const tip = r.last_scan_time ? '最近探测：' + r.last_scan_time : '尚未执行在线探测';
  if (!r.last_scan_time) return '<span class="badge" title="' + attr(tip) + '">未探测</span>';
  return r.is_online
    ? '<span class="badge in_use" title="' + attr(tip) + '">在线</span>'
    : '<span class="badge" title="' + attr(tip) + '">离线</span>';
}

async function renderCrud(key) {
  const cfg = RES[key];
  if (!cfg) { $('#view').innerHTML = '<div class="card">未知页面</div>'; return; }
  $('#view').innerHTML = '<div class="card"><div class="empty">加载中…</div></div>';
  let data;
  try { data = await api('GET', cfg.endpoint + '?' + queryString(cfg)); }
  catch (e) { $('#view').innerHTML = '<div class="card"><div class="empty">' + esc(e.message) + '</div></div>'; return; }
  S.pageIds = data.items.map(x => x.id);

  const canWrite = !(cfg.adminOnly && S.user.role !== 'admin');
  let html = '<div class="toolbar">';
  html += '<input type="search" id="listSearch" placeholder="搜索…" value="' + attr(S.q) + '">';
  (cfg.filters || []).forEach(f => {
    html += '<select data-filter="' + attr(f.k) + '"><option value="">' + esc(f.t) + '：全部</option>' +
      opts(typeof f.options === 'function' ? f.options() : f.options, S.filters[f.k] || '', undefined) + '</select>';
  });
  html += '<button class="btn btn-sm" id="btnRefresh">刷新</button><span class="grow"></span>';
  if (canWrite) html += '<button class="btn btn-primary btn-sm" id="btnAdd">+ 新增' + esc(cfg.name) + '</button>';
  html += '</div>';

  if (cfg.batch && canWrite) {
    html += '<div class="batch-bar" id="batchBar"><strong class="small">已选 <span id="selCount">0</span> 项</strong>';
    html += '<select id="batchStatus">' + opts(statusOpts(), '', '改为状态…') + '</select>';
    html += '<select id="batchOrg">' + opts(orgOpts(), '', '改为单位…') + '</select>';
    html += '<input type="text" id="batchOwner" placeholder="使用人" style="width:110px">';
    html += '<select id="batchTag">' + opts(tagOpts(), '', '绑定标签…') + '</select>';
    html += '<button class="btn btn-sm" data-batch="owner">改使用人</button>';
    html += '<button class="btn btn-sm" data-batch="recycle">回收</button>';
    html += '<button class="btn btn-sm" data-batch="reserve">预留</button>';
    html += '<button class="btn btn-sm" data-batch="probe">在线探测</button>';
    html += '<button class="btn btn-sm btn-danger" data-batch="delete">删除</button></div>';
  }

  html += '<div class="table-wrap"><table><thead><tr>';
  if (cfg.batch && canWrite) html += '<th style="width:34px"><input type="checkbox" id="checkAll"></th>';
  cfg.cols.forEach(c => {
    html += '<th class="' + (c.sort ? 'sortable' : '') + '"' + (c.sort ? ' data-sort="' + attr(c.k) + '"' : '') + '>' +
      esc(c.t) + (S.sort === c.k ? (S.order === 'asc' ? ' ↑' : ' ↓') : '') + '</th>';
  });
  html += '<th style="width:150px">操作</th></tr></thead><tbody>';
  if (!data.items.length) {
    html += '<tr><td colspan="' + (cfg.cols.length + 1 + (cfg.batch ? 1 : 0)) + '"><div class="empty">暂无数据，点击右上角新增</div></td></tr>';
  }
  data.items.forEach(r => {
    html += '<tr>';
    if (cfg.batch && canWrite) html += '<td><input type="checkbox" class="row-check" value="' + attr(r.id) + '"' +
      (S.selected.has(r.id) ? ' checked' : '') + '></td>';
    cfg.cols.forEach(c => { html += '<td>' + cell(r, c) + '</td>'; });
    html += '<td class="actions">';
    (cfg.rowActions || []).forEach((a, i) => { html += '<button class="btn btn-sm" data-rowact="' + i + '" data-id="' + attr(r.id) + '">' + esc(a.t) + '</button> '; });
    if (canWrite) html += '<button class="btn btn-sm" data-edit="' + attr(r.id) + '">编辑</button> ' +
      '<button class="btn btn-sm btn-danger" data-del="' + attr(r.id) + '" data-name="' + attr(r.name || r.ip_address || r.cidr || r.asset_name || r.username || r.id) + '">删除</button>';
    html += '</td></tr>';
  });
  html += '</tbody></table></div>';
  html += '<div class="pager">共 ' + fmt(data.total) + ' 条 · 第 ' + data.page + '/' + data.pages + ' 页 ' +
    '<button class="btn btn-sm" id="pagePrev"' + (data.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
    '<button class="btn btn-sm" id="pageNext"' + (data.page >= data.pages ? ' disabled' : '') + '>下一页</button>' +
    '<select id="perPage">' + opts([20, 30, 50, 100, 200], S.perPage, undefined) + '</select></div>';
  $('#view').innerHTML = html;

  $('#listSearch').addEventListener('change', e => { S.q = e.target.value.trim(); S.page = 1; renderCrud(key); });
  $('#btnRefresh').onclick = () => renderCrud(key);
  $$('[data-filter]').forEach(el => el.onchange = () => {
    S.filters[el.dataset.filter] = el.value; S.page = 1; renderCrud(key);
  });
  $$('[data-sort]').forEach(el => el.onclick = () => {
    if (S.sort === el.dataset.sort) S.order = S.order === 'asc' ? 'desc' : 'asc';
    else { S.sort = el.dataset.sort; S.order = 'asc'; }
    renderCrud(key);
  });
  $('#pagePrev').onclick = () => { if (S.page > 1) { S.page--; renderCrud(key); } };
  $('#pageNext').onclick = () => { S.page++; renderCrud(key); };
  $('#perPage').onchange = e => { S.perPage = Number(e.target.value); S.page = 1; renderCrud(key); };

  if ($('#btnAdd')) $('#btnAdd').onclick = () => openForm(cfg, null, key);
  $$('[data-edit]').forEach(b => b.onclick = () => openForm(cfg, data.items.find(x => x.id === b.dataset.edit), key));
  $$('[data-del]').forEach(b => b.onclick = () => confirmBox('确定删除「' + b.dataset.name + '」？此操作不可撤销。', async () => {
    await api('DELETE', cfg.endpoint + '/' + b.dataset.del);
    toast('已删除'); await loadOptions(); renderCrud(key);
  }));
  $$('[data-rowact]').forEach(b => b.onclick = () => {
    const row = data.items.find(x => x.id === b.dataset.id);
    cfg.rowActions[Number(b.dataset.rowact)].fn(row);
  });

  if (cfg.batch && canWrite) {
    $$('.row-check').forEach(c => c.onchange = () => {
      if (c.checked) S.selected.add(c.value); else S.selected.delete(c.value);
      $('#selCount').textContent = S.selected.size;
    });
    $('#checkAll').onchange = e => {
      $$('.row-check').forEach(c => { c.checked = e.target.checked; if (c.checked) S.selected.add(c.value); else S.selected.delete(c.value); });
      $('#selCount').textContent = S.selected.size;
    };
    $('#selCount').textContent = S.selected.size;
    $('#batchStatus').onchange = async e => { if (e.target.value) await batch(key, 'status', e.target.value); };
    $('#batchOrg').onchange = async e => { if (e.target.value) await batch(key, 'organization', e.target.value); };
    $('#batchTag').onchange = async e => { if (e.target.value) await batch(key, 'bind_tags', null, [e.target.value]); };
    $$('[data-batch]').forEach(b => b.onclick = async () => {
      const act = b.dataset.batch;
      if (act === 'owner') {
        const v = $('#batchOwner').value.trim();
        if (!v) return toast('请填写使用人', 'err');
        await batch(key, 'owner', v);
      } else if (act === 'delete') {
        confirmBox('确定删除选中的 ' + S.selected.size + ' 条记录？', () => batch(key, 'delete', null));
      } else await batch(key, act, null);
    });
  }
}

async function batch(key, action, value, tags) {
  let ids = Array.from(S.selected);
  if (!ids.length) {
    /* 在线探测未勾选任何记录时自动探测当前页全部记录，
       避免「已选 0 项」时按钮看似点了却什么都没发生。 */
    if (action === 'probe' && S.pageIds.length) ids = S.pageIds.slice();
    else return toast('请先选择记录', 'err');
  }
  try {
    const body = { ids, value };
    if (tags) body.tags = tags;
    const path = action === 'probe' ? 'records/probe' : 'records/batch_' + action;
    if (action === 'probe') toast('正在探测 ' + ids.length + ' 个地址…');
    const out = await api('POST', path, body);
    toast(out.detail || ('探测完成：在线 ' + out.online + ' 条'));
    S.selected.clear(); renderCrud(key);
  } catch (e) { toast(e.message, 'err'); }
}

function openForm(cfg, row, key) {
  const edit = !!row;
  const fields = cfg.fields();
  let values = row ? Object.assign({}, row) : {};
  if (key === 'records' && edit) {
    values.tagIds = (row.tags || []).map(t => t.id);
    if (!values.tags) {
      // 行数据不含 tags 明细，按需从详情接口补齐
      api('GET', 'records/' + row.id).then(d => {
        values.tagIds = (d.tags || []).map(t => t.id);
        const sel = $('#f_tags');
        if (sel) Array.from(sel.options).forEach(o => { o.selected = values.tagIds.includes(o.value); });
      }).catch(() => {});
    }
  }
  if (key === 'users' && edit) values.password = '';
  if (key === 'ip_pools' && !edit) values = { range_start: '192.168.1.100', range_end: '192.168.1.200' };
  modal((edit ? '编辑' : '新增') + cfg.name, formHtml(fields, values),
    '<button class="btn" data-close>取消</button><button class="btn btn-primary" id="saveForm">保存</button>');
  $('#saveForm').onclick = async () => {
    const payload = readForm();
    Object.keys(payload).forEach(k => { if (payload[k] === '' && ['org_id', 'department_id', 'subnet_id', 'vlan_id', 'parent_id'].includes(k)) payload[k] = null; });
    if (key === 'users') {
      payload.is_active = Number(payload.is_active);
      if (!payload.password) delete payload.password;
    }
    if (key === 'vlans' || key === 'records') if (payload.port !== undefined) payload.port = Number(payload.port || 0);
    if (key === 'vlans') payload.vlan_id = Number(payload.vlan_id);
    try {
      await api(edit ? 'PUT' : 'POST', cfg.endpoint + (edit ? '/' + row.id : ''), payload);
      closeModal(); toast(edit ? '已更新' : '已创建'); await loadOptions(); renderCrud(key);
    } catch (e) { toast(e.message, 'err'); }
  };
}

/* ---------------- 行操作 ---------------- */

function allocatePool(r) {
  modal('从地址池分配 · ' + r.name, formHtml([{ k: 'device_name', t: '设备名称（可选）', full: true }], {}),
    '<button class="btn" data-close>取消</button><button class="btn btn-primary" id="doAlloc">分配</button>');
  $('#doAlloc').onclick = async () => {
    try {
      const out = await api('POST', 'ip_pools/' + r.id + '/allocate', readForm());
      closeModal(); toast('已分配 ' + out.ip_address); renderCrud('ip_pools');
    } catch (e) { toast(e.message, 'err'); }
  };
}
function releasePool(r) {
  modal('回收地址 · ' + r.name, formHtml([{ k: 'ip_address', t: '要回收的 IP', required: true, full: true }], {}),
    '<button class="btn" data-close>取消</button><button class="btn btn-primary" id="doRelease">回收</button>');
  $('#doRelease').onclick = async () => {
    try {
      const out = await api('POST', 'ip_pools/' + r.id + '/release', readForm());
      closeModal(); toast('已回收 ' + out.released); renderCrud('ip_pools');
    } catch (e) { toast(e.message, 'err'); }
  };
}
async function assetBindings(r) {
  let data;
  try { data = await api('GET', 'assets/' + r.id + '/bindings'); }
  catch (e) { return toast(e.message, 'err'); }
  let html = '<div class="table-wrap"><table><thead><tr><th>IP 地址</th><th>设备名称</th><th>状态</th><th>操作</th></tr></thead><tbody>';
  data.items.forEach(b => {
    html += '<tr><td class="mono">' + esc(b.ip_address) + '</td><td>' + esc(b.device_name) + '</td><td>' + esc(b.status_text) +
      '</td><td><button class="btn btn-sm btn-danger" data-unbind="' + attr(b.id) + '">解除</button></td></tr>';
  });
  if (!data.items.length) html += '<tr><td colspan="4"><div class="empty">尚未绑定 IP</div></td></tr>';
  html += '</tbody></table></div>';
  try {
    const options = await api('GET', 'assets/' + r.id + '/bindings/options');
    window._bindOptions = options.items;
    html += '<div style="margin-top:14px;display:flex;gap:8px"><select id="bindRecord" style="flex:1">' +
      opts(options.items.map(x => ({ value: x.id, label: x.ip_address + ' · ' + (x.device_name || '未命名') })), '', '选择要绑定的 IP 记录') +
      '</select><button class="btn btn-primary" id="doBind">绑定</button></div>';
  } catch (e) { /* ignore */ }
  modal('资产绑定 IP · ' + r.asset_name, html, '<button class="btn" data-close>关闭</button>');
  $$('[data-unbind]').forEach(b => b.onclick = async () => {
    try { await api('DELETE', 'assets/' + r.id + '/bindings/' + b.dataset.unbind); closeModal(); toast('已解除绑定'); assetBindings(r); }
    catch (e) { toast(e.message, 'err'); }
  });
  if ($('#doBind')) $('#doBind').onclick = async () => {
    const id = $('#bindRecord').value;
    if (!id) return toast('请选择 IP 记录', 'err');
    try { await api('POST', 'assets/' + r.id + '/bindings', { record_id: id }); closeModal(); toast('已绑定'); renderCrud('assets'); }
    catch (e) { toast(e.message, 'err'); }
  };
}

/* ---------------- 特殊视图 ---------------- */

async function renderDashboard() {
  let d;
  try { d = await api('GET', 'dashboard/stats'); } catch (e) { return toast(e.message, 'err'); }
  const cards = [
    ['IP 记录', d.records, 'in_use'], ['使用中', d.in_use, 'idle'], ['空闲', d.idle, ''],
    ['预留', d.reserved, 'reserved'], ['在线', d.online, 'ok'], ['子网', d.subnets, ''],
    ['VLAN', d.vlans, ''], ['资产', d.assets, ''], ['单位', d.organizations, ''], ['审计记录', d.audit_logs, ''],
  ];
  let html = '<div class="stat-grid">';
  cards.forEach(([label, val]) => { html += '<div class="stat-card"><div class="num">' + fmt(val) + '</div><div class="lbl">' + esc(label) + '</div><div class="bar"><i style="width:' + Math.min(100, Number(val) ? 18 + Number(val) : 0) + '%"></i></div></div>'; });
  html += '</div><div class="two-col">';
  html += '<div class="card"><div class="card-title">最近新增记录</div><div class="table-wrap"><table><thead><tr><th>IP 地址</th><th>设备名称</th><th>时间</th></tr></thead><tbody>';
  d.recent_records.forEach(r => { html += '<tr><td class="mono">' + esc(r.ip_address) + '</td><td>' + esc(r.device_name) + '</td><td class="small dim">' + esc(r.created_at) + '</td></tr>'; });
  if (!d.recent_records.length) html += '<tr><td colspan="3"><div class="empty">暂无记录</div></td></tr>';
  html += '</tbody></table></div></div>';
  html += '<div class="card"><div class="card-title">最近操作</div><div class="table-wrap"><table><thead><tr><th>操作</th><th>详情</th><th>操作者</th></tr></thead><tbody>';
  d.recent_logs.forEach(r => { html += '<tr><td>' + esc(r.action) + '</td><td class="small">' + esc(r.detail) + '</td><td class="small dim">' + esc(r.operator) + '</td></tr>'; });
  if (!d.recent_logs.length) html += '<tr><td colspan="3"><div class="empty">暂无记录</div></td></tr>';
  html += '</tbody></table></div></div></div>';
  $('#view').innerHTML = html;
}

async function renderStatistics() {
  let util, conflicts, dist, sys;
  try {
    [util, conflicts, dist, sys] = await Promise.all([
      api('GET', 'statistics/utilization'), api('GET', 'statistics/conflicts'),
      api('GET', 'statistics/asset_distribution'), api('GET', 'system/stats')]);
  } catch (e) { return toast(e.message, 'err'); }
  S.sysStats = sys;
  let html = '<div class="stat-grid"><div class="stat-card"><div class="num">' + fmt(util.summary.used) + '</div><div class="lbl">已分配地址</div></div>' +
    '<div class="stat-card"><div class="num">' + fmt(util.summary.total) + '</div><div class="lbl">子网可用地址总数</div></div>' +
    '<div class="stat-card"><div class="num">' + conflicts.total + '</div><div class="lbl">冲突与异常</div></div>' +
    '<div class="stat-card"><div class="num">' + esc(sys.db_size_text) + '</div><div class="lbl">数据库大小</div></div></div>';
  html += '<div class="card"><div class="card-title">子网地址利用率</div>';
  if (!util.items.length) html += '<div class="empty">暂无子网</div>';
  util.items.forEach(u => {
    html += '<div class="bar-row"><span class="mono">' + esc(u.cidr) + '</span><div class="progress"><i style="width:' + u.utilization + '%"></i></div><span>' + u.used + '/' + u.total + ' · ' + u.utilization + '%</span></div>';
  });
  html += '</div>';
  html += '<div class="two-col"><div class="card"><div class="card-title">冲突与异常检测</div>';
  if (!conflicts.items.length) html += '<div class="empty">未发现冲突</div>';
  conflicts.items.slice(0, 30).forEach(c => {
    html += '<div style="padding:6px 0;border-bottom:1px solid var(--border)"><span class="badge ' + attr(c.level) + '">' + esc(c.level) + '</span> ' + esc(c.detail) + '</div>';
  });
  html += '</div><div class="card"><div class="card-title">资产类型分布</div>';
  const max = Math.max(1, ...dist.by_type.map(x => x.c));
  dist.by_type.forEach(x => {
    html += '<div class="bar-row"><span>' + esc(x.label) + '</span><div class="progress"><i style="width:' + (x.c / max * 100) + '%"></i></div><span>' + x.c + ' 台</span></div>';
  });
  if (!dist.by_type.length) html += '<div class="empty">暂无资产</div>';
  html += '</div></div>';
  $('#view').innerHTML = html;
}

function renderImportExport() {
  const cols = 'IP地址,端口,设备名称,设备类型,MAC地址,状态,使用人,单位,部门,子网,业务系统,备注';
  let html = '<div class="tabs"><button class="tab active" data-tab="imp">导入 CSV</button><button class="tab" data-tab="exp">导出与模板</button></div><div id="tabBody"></div>';
  $('#view').innerHTML = html;
  const imp = () => {
    $('#tabBody').innerHTML = '<div class="card"><div class="card-title">导入 IP 记录 / 资产 CSV</div>' +
      '<div class="hint">支持 UTF-8 / GBK 编码，逗号或分号分隔。中英文列名均可识别。导入前会预览并校验，重复记录自动跳过。</div>' +
      '<div class="toolbar"><select id="importKind"><option value="records">IP 记录</option><option value="assets">资产设备</option></select>' +
      '<input type="file" id="csvFile" accept=".csv,text/csv"><button class="btn btn-sm" id="btnTpl">下载模板</button></div>' +
      '<div id="preview"></div></div>';
    $('#btnTpl').onclick = () => {
      const kind = $('#importKind').value;
      const head = kind === 'records' ? cols : '资产编号,资产名称,资产类型,品牌,型号,序列号,状态,位置,负责人,购买日期,保修到期,备注';
      const blob = new Blob(['\ufeff' + head + '\n'], { type: 'text/csv;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = kind + '_template.csv'; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    };
    $('#csvFile').onchange = async e => {
      const file = e.target.files[0]; if (!file) return;
      if (file.size > 10 * 1024 * 1024) return toast('文件不能超过 10MB', 'err');
      const buf = await file.arrayBuffer();
      let text;
      try { text = new TextDecoder('utf-8', { fatal: true }).decode(buf); }
      catch (err) { try { text = new TextDecoder('gbk').decode(buf); } catch (e2) { return toast('文件编码无法识别', 'err'); } }
      try {
        const d = await api('POST', 'import_csv/preview', { csv: text, kind: $('#importKind').value });
        S.importData = d;
        let p = '<div class="card" style="margin-top:14px"><div class="card-title">预览：共 ' + d.total + ' 行，可导入 ' + d.valid + ' 行，异常 ' + d.invalid + ' 行</div>';
        p += '<div class="table-wrap"><table><thead><tr><th>行</th><th>IP/编号</th><th>名称</th><th>状态</th><th>校验结果</th></tr></thead><tbody>';
        d.rows.slice(0, 100).forEach(r => {
          p += '<tr><td>' + r._line + '</td><td class="mono">' + esc(r.ip_address || r.asset_number) + '</td><td>' +
            esc(r.device_name || r.asset_name) + '</td><td>' + esc(r.status) + '</td><td>' +
            (r._errors.length ? '<span class="badge disabled">' + esc(r._errors.join('；')) + '</span>' : '<span class="badge in_use">可导入</span>') + '</td></tr>';
        });
        p += '</tbody></table></div><div class="toolbar" style="margin-top:12px">' +
          '<button class="btn btn-primary" id="doImport">确认导入 ' + d.valid + ' 行</button>' +
          (d.invalid ? '<button class="btn" id="fixCsv">下载异常行</button>' : '') + '</div></div>';
        $('#preview').innerHTML = p;
        $('#doImport').onclick = async () => {
          try {
            const out = await api('POST', 'import_csv', { rows: d.rows, kind: $('#importKind').value });
            toast('导入完成：新增 ' + out.added + ' 条，跳过 ' + out.skipped + ' 条');
            await loadOptions();
          } catch (err) { toast(err.message, 'err'); }
        };
        if ($('#fixCsv')) $('#fixCsv').onclick = async () => {
          try {
            const blob = await fetch('api/import_csv/fix', {
              method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ rows: d.rows, kind: $('#importKind').value })
            }).then(r => r.blob());
            const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
            a.download = 'import_fix.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
          } catch (err) { toast(err.message, 'err'); }
        };
      } catch (err) { toast(err.message, 'err'); }
    };
  };
  const exp = () => {
    $('#tabBody').innerHTML = '<div class="card"><div class="card-title">导出数据</div>' +
      '<p class="muted small">导出为 UTF-8 BOM 编码的 CSV，可直接用 Excel / WPS 打开。</p>' +
      '<div class="toolbar"><a class="btn btn-primary" href="api/export_csv?type=records" download>导出 IP 记录 CSV</a>' +
      '<a class="btn" href="api/assets/export_csv" download>导出资产 CSV</a></div>' +
      '<div class="hint">IP 记录列名：' + esc(cols) + '</div></div>';
  };
  $$('.tab').forEach(t => t.onclick = () => {
    $$('.tab').forEach(x => x.classList.remove('active')); t.classList.add('active');
    t.dataset.tab === 'imp' ? imp() : exp();
  });
  imp();
}

async function renderBackup() {
  let d;
  try { d = await api('GET', 'backups'); } catch (e) { return toast(e.message, 'err'); }
  let html = '<div class="card"><div class="card-title"><span>数据库备份</span><button class="btn btn-primary btn-sm" id="btnBackup">+ 立即备份</button></div>' +
    '<div class="hint">备份文件保存在应用数据目录，恢复前会自动为当前数据库再生成一份安全备份。恢复后请刷新页面。</div>' +
    '<div class="table-wrap"><table><thead><tr><th>文件名</th><th>大小</th><th>创建时间</th><th>操作</th></tr></thead><tbody>';
  d.items.forEach(b => {
    html += '<tr><td class="mono">' + esc(b.name) + '</td><td>' + esc(b.size_text) + '</td><td>' + esc(b.created_at) +
      '</td><td class="actions"><a class="btn btn-sm" href="api/backup/' + attr(b.name) + '" download>下载</a> ' +
      '<button class="btn btn-sm" data-restore="' + attr(b.name) + '">恢复</button> ' +
      '<button class="btn btn-sm btn-danger" data-delbk="' + attr(b.name) + '">删除</button></td></tr>';
  });
  if (!d.items.length) html += '<tr><td colspan="4"><div class="empty">暂无备份</div></td></tr>';
  html += '</tbody></table></div></div>';
  $('#view').innerHTML = html;
  $('#btnBackup').onclick = async () => {
    try { const o = await api('POST', 'backup', {}); toast('备份完成：' + o.name); renderBackup(); }
    catch (e) { toast(e.message, 'err'); }
  };
  $$('[data-restore]').forEach(b => b.onclick = () => confirmBox('确定从「' + b.dataset.restore + '」恢复？当前数据将被覆盖（系统会自动另存一份安全备份）。', async () => {
    await api('POST', 'backup/' + b.dataset.restore + '/restore', {}); toast('恢复完成，请刷新页面'); renderBackup();
  }));
  $$('[data-delbk]').forEach(b => b.onclick = () => confirmBox('确定删除备份「' + b.dataset.delbk + '」？', async () => {
    await api('DELETE', 'backup/' + b.dataset.delbk); toast('已删除'); renderBackup();
  }));
}

async function renderAudit() {
  let d;
  try { d = await api('GET', 'audit_logs?page=' + S.page + '&per_page=' + S.perPage + '&q=' + encodeURIComponent(S.q)); }
  catch (e) { return toast(e.message, 'err'); }
  let html = '<div class="toolbar"><input type="search" id="auditSearch" placeholder="搜索操作 / 详情 / 操作者" value="' + attr(S.q) + '">' +
    '<button class="btn btn-sm" id="auditRefresh">刷新</button><span class="grow"></span>' +
    (S.user.role === 'admin' ? '<button class="btn btn-sm btn-danger" id="auditClear">清空日志</button>' : '') + '</div>';
  html += '<div class="table-wrap"><table><thead><tr><th>时间</th><th>操作</th><th>对象</th><th>详情</th><th>操作者</th></tr></thead><tbody>';
  d.items.forEach(r => {
    html += '<tr><td class="small mono">' + esc(r.created_at) + '</td><td>' + esc(r.action) + '</td><td class="mono">' +
      esc(r.target) + '</td><td>' + esc(r.detail) + '</td><td>' + esc(r.operator) + '</td></tr>';
  });
  if (!d.items.length) html += '<tr><td colspan="5"><div class="empty">暂无日志</div></td></tr>';
  html += '</tbody></table></div><div class="pager">共 ' + d.total + ' 条 · 第 ' + d.page + '/' + d.pages + ' 页 ' +
    '<button class="btn btn-sm" id="auditPrev"' + (d.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
    '<button class="btn btn-sm" id="auditNext"' + (d.page >= d.pages ? ' disabled' : '') + '>下一页</button></div>';
  $('#view').innerHTML = html;
  $('#auditSearch').onchange = e => { S.q = e.target.value.trim(); S.page = 1; renderAudit(); };
  $('#auditRefresh').onclick = renderAudit;
  $('#auditPrev').onclick = () => { if (S.page > 1) { S.page--; renderAudit(); } };
  $('#auditNext').onclick = () => { S.page++; renderAudit(); };
  if ($('#auditClear')) $('#auditClear').onclick = () => confirmBox('确定清空全部审计日志？', async () => {
    await api('POST', 'audit_logs/clear', {}); toast('已清空'); renderAudit();
  });
}

async function renderSettings() {
  let info;
  try { info = await api('GET', 'settings/info'); } catch (e) { return toast(e.message, 'err'); }
  /* 系统名称 / 界面模式 / 版权与联系信息不在本页展示，也不提供修改入口：
     统一由安装向导、配置向导与 branding.json 管理。 */
  let html = '';
  html += '<div class="card"><div class="card-title">字段显示名称</div><p class="muted small">自定义列表与表单中字段的显示文字。</p><div id="labelRows"></div>' +
    '<div style="margin-top:12px"><button class="btn btn-primary" id="saveLabels">保存字段名称</button></div></div>';
  html += '<div class="card"><div class="card-title">修改登录密码</div><div class="toolbar">' +
    '<input type="password" id="oldPass" placeholder="原密码"><input type="password" id="newPass" placeholder="新密码（至少 6 位）">' +
    '<button class="btn btn-primary" id="savePass">修改</button></div></div>';
  $('#view').innerHTML = html;

  const defaults = { ip_address: 'IP 地址', port: '端口', device_name: '设备名称', device_type: '设备类型',
    mac_address: 'MAC 地址', owner: '使用人', organization: '单位', department: '部门', status: '状态',
    business_system: '业务系统', subnet: '子网', remark: '备注' };
  const labels = Object.assign({}, defaults, info.field_labels || {});
  $('#labelRows').innerHTML = '<div class="form-grid">' + Object.keys(defaults).map(k =>
    '<div><label>' + esc(defaults[k]) + ' <span class="dim mono">' + esc(k) + '</span></label>' +
    '<input data-label="' + attr(k) + '" value="' + attr(labels[k]) + '"></div>').join('') + '</div>';

  $('#saveLabels').onclick = async () => {
    const obj = {}; $$('[data-label]').forEach(el => obj[el.dataset.label] = el.value);
    try { await api('POST', 'settings/field_labels', { field_labels: obj }); toast('已保存'); }
    catch (e) { toast(e.message, 'err'); }
  };
  $('#savePass').onclick = async () => {
    try { await api('POST', 'change_password', { old_password: $('#oldPass').value, new_password: $('#newPass').value });
      $('#oldPass').value = ''; $('#newPass').value = ''; toast('密码已修改');
    } catch (e) { toast(e.message, 'err'); }
  };
}

async function renderAbout() {
  let sys;
  try { sys = await api('GET', 'system/stats'); } catch (e) { sys = { version: S.session.version, python: '-', tables: {}, db_size_text: '-' }; }
  const b = S.branding || {};
  const rows = [
    ['应用名称', S.appName], ['版本', sys.version || S.session.version], ['运行环境', 'Python ' + (sys.python || '-')],
    ['数据存储', '本地 SQLite · ' + (sys.db_size_text || '-')], ['作者 / 维护者', b.owner_name || '梦非常浪漫'],
    ['联系邮箱', b.contact_email || '12031050@qq.com'], ['QQ群 / 其他联系方式', b.contact || 'QQ群：416290431'],
    ['版权声明', b.copyright || '© 2026 梦非常浪漫'],
    ['开源地址', b.source_url || SOURCE_DEFAULT], ['运行平台', '飞牛 fnOS 应用'],
  ];
  let html = '<div class="card"><div class="card-title">关于本应用</div>' +
    '<p class="muted">一套本地部署的 IP 地址与资产台账工具，覆盖 IP 记录、子网规划、VLAN、地址池、资产绑定、统计、导入导出与备份。数据全部保存在 NAS 本地，不依赖外部服务。</p><dl class="kv">';
  rows.forEach(([k, v]) => {
    if (v === undefined) return;
    const text = String(v == null ? '' : v);
    const val = (k === '开源地址' && isHttpUrl(text)) ? linkHtml(text) : esc(text);
    html += '<dt>' + esc(k) + '</dt><dd>' + val + '</dd>';
  });
  html += '</dl></div><div class="card"><div class="card-title">免责声明</div><p class="muted">' + esc(b.disclaimer || '') + '</p></div>';
  $('#view').innerHTML = html;
}

const SPECIAL = {
  dashboard: renderDashboard, statistics: renderStatistics, import_export: renderImportExport,
  backup: renderBackup, audit: renderAudit, settings: renderSettings, about: renderAbout,
};

boot();
