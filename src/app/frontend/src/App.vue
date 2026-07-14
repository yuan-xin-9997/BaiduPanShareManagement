<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

const bootstrap = ref(null)
const user = ref(null)
const activeView = ref('shares')
const state = reactive({ links: [], mappings: [], tasks: [], runs: [], cookie_configured: false })
const loginForm = reactive({ username: '', password: '' })
const loginError = ref('')
const toastText = ref('')
const shareModal = ref(false)
const shareForm = reactive({ id: null, url: '', password: '', note: '' })
const mappingModal = ref(false)
const mappingForm = reactive({ id: null, share_link_id: '', remote_path: '', local_path: '', storage_type: 'local', sync_strategy: 'copy_new', schedule_interval: 60, auto_sync: false, last_synced: 0 })
const browser = reactive({ open: false, linkId: null, title: '', path: '', entries: [] })
const settings = ref(null)
const cookieValue = ref('')
const users = ref([])
const userModal = ref(false)
const userForm = reactive({ id: null, username: '', password: '', role: 'user', pages: ['shares', 'mappings', 'tasks'] })
const probeMessage = ref('')
let refreshTimer

const pageMeta = {
  shares: ['分享链接', '管理和浏览百度网盘分享目录'],
  mappings: ['同步映射', '将远端目录安全同步到本机或 NAS'],
  tasks: ['任务中心', '查看实时任务与历史同步日志'],
  settings: ['系统配置', '查看运行配置并维护百度网盘凭据'],
  users: ['权限管理', '维护用户角色与可访问页面'],
}
const navItems = computed(() => [
  ['shares', '链', '分享链接'], ['mappings', '映', '同步映射'],
  ['tasks', '任', '任务中心'], ['settings', '设', '系统配置'],
  ['users', '权', '权限管理'],
].filter(([key]) => user.value?.pages?.includes(key)))
const currentTitle = computed(() => pageMeta[activeView.value] || ['', ''])

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })
  let payload = {}
  try { payload = await response.json() } catch {}
  if (response.status === 401) { user.value = null; throw new Error('登录状态已失效') }
  if (!response.ok) throw new Error(payload.detail || `请求失败 (${response.status})`)
  return payload
}
function showToast(message) {
  toastText.value = message
  setTimeout(() => { if (toastText.value === message) toastText.value = '' }, 3500)
}
function formatTime(value) { return value ? new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '从未' }
function formatSize(value) {
  let size = Number(value || 0)
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (size < 1024) return `${size.toFixed(1)} ${unit}`
    size /= 1024
  }
  return `${size.toFixed(1)} PB`
}
async function boot() {
  bootstrap.value = await api('/api/bootstrap')
  if (bootstrap.value.authenticated) {
    user.value = bootstrap.value.user
    activeView.value = user.value.pages[0] || 'shares'
    await refresh()
  }
}
async function login() {
  loginError.value = ''
  try {
    const result = await api('/api/login', { method: 'POST', body: JSON.stringify(loginForm) })
    user.value = result.user
    activeView.value = user.value.pages[0] || 'shares'
    loginForm.password = ''
    await refresh()
  } catch (error) { loginError.value = error.message }
}
async function logout() { await api('/api/logout', { method: 'POST' }); user.value = null }
async function refresh() {
  if (!user.value) return
  const result = await api('/api/state')
  user.value = result.user
  Object.assign(state, result)
}
async function switchView(view) {
  activeView.value = view
  if (view === 'settings') settings.value = await api('/api/settings')
  if (view === 'users') users.value = await api('/api/users')
}

function openShare(link = null) {
  Object.assign(shareForm, { id: link?.id || null, url: link?.url || '', password: '', note: link?.note || '' })
  shareModal.value = true
}
async function saveShare() {
  const body = { url: shareForm.url, note: shareForm.note }
  if (!shareForm.id || shareForm.password) body.password = shareForm.password
  try {
    await api(shareForm.id ? `/api/shares/${shareForm.id}` : '/api/shares', { method: shareForm.id ? 'PUT' : 'POST', body: JSON.stringify(body) })
    shareModal.value = false
    showToast(shareForm.id ? '分享链接已保存' : '链接索引任务已提交')
    await refresh()
  } catch (error) { showToast(error.message) }
}
async function refreshShare(id) { try { await api(`/api/shares/${id}/refresh`, { method: 'POST' }); showToast('刷新任务已提交'); await refresh() } catch (error) { showToast(error.message) } }
async function deleteShare(id) { if (!confirm('删除分享链接、索引和相关映射？本地文件不会被删除。')) return; try { await api(`/api/shares/${id}`, { method: 'DELETE' }); await refresh() } catch (error) { showToast(error.message) } }
async function browse(link, path = '') {
  browser.open = true; browser.linkId = link.id; browser.title = link.title; browser.path = path
  browser.entries = await api(`/api/shares/${link.id}/entries?parent=${encodeURIComponent(path)}`)
}
const crumbs = computed(() => {
  let built = ''
  return browser.path.split('/').filter(Boolean).map(name => ({ name, path: built += `/${name}` }))
})

function openMapping(mapping = null) {
  Object.assign(mappingForm, {
    id: mapping?.id || null, share_link_id: mapping?.share_link_id || state.links[0]?.id || '',
    remote_path: mapping?.remote_path || '', local_path: mapping?.local_path || '',
    storage_type: mapping?.storage_type || 'local', sync_strategy: mapping?.sync_strategy || 'copy_new',
    schedule_interval: mapping?.schedule_interval || 60, auto_sync: mapping?.auto_sync || false,
    last_synced: mapping?.last_synced || 0,
  })
  probeMessage.value = ''; mappingModal.value = true
}
async function saveMapping() { try { await api(mappingForm.id ? `/api/mappings/${mappingForm.id}` : '/api/mappings', { method: mappingForm.id ? 'PUT' : 'POST', body: JSON.stringify(mappingForm) }); mappingModal.value = false; await refresh() } catch (error) { showToast(error.message) } }
async function probeStorage(mapping = mappingForm) { try { const result = await api('/api/storage/probe', { method: 'POST', body: JSON.stringify(mapping) }); const text = `${result.ok ? '✓' : '×'} ${result.message}`; if (mapping === mappingForm) probeMessage.value = text; else showToast(text) } catch (error) { showToast(error.message) } }
async function syncOne(id) { try { await api(`/api/mappings/${id}/sync`, { method: 'POST' }); showToast('同步任务已提交'); await refresh() } catch (error) { showToast(error.message) } }
async function syncAll() { try { const result = await api('/api/sync-all', { method: 'POST' }); showToast(`已提交 ${result.task_ids.length} 个任务`); await refresh() } catch (error) { showToast(error.message) } }
async function deleteMapping(id) { if (!confirm('删除此同步映射？本地文件不会被删除。')) return; try { await api(`/api/mappings/${id}`, { method: 'DELETE' }); await refresh() } catch (error) { showToast(error.message) } }

async function saveSettings() { try { await api('/api/settings', { method: 'PUT', body: JSON.stringify({ cookie: cookieValue.value }) }); cookieValue.value = ''; settings.value = await api('/api/settings'); showToast('百度网盘 Cookie 已保存') } catch (error) { showToast(error.message) } }
function openUser(account = null) {
  Object.assign(userForm, { id: account?.id || null, username: account?.username || '', password: '', role: account?.role || 'user', pages: [...(account?.pages || ['shares', 'mappings', 'tasks'])] })
  userModal.value = true
}
function togglePage(page) { userForm.pages = userForm.pages.includes(page) ? userForm.pages.filter(item => item !== page) : [...userForm.pages, page] }
async function saveUser() { try { await api(userForm.id ? `/api/users/${userForm.id}` : '/api/users', { method: userForm.id ? 'PUT' : 'POST', body: JSON.stringify(userForm) }); userModal.value = false; users.value = await api('/api/users'); showToast('用户与权限已保存') } catch (error) { showToast(error.message) } }
async function deleteUser(id) { if (!confirm('删除该用户？')) return; try { await api(`/api/users/${id}`, { method: 'DELETE' }); users.value = await api('/api/users') } catch (error) { showToast(error.message) } }

onMounted(async () => { await boot(); refreshTimer = setInterval(() => refresh().catch(() => {}), 3000) })
onBeforeUnmount(() => clearInterval(refreshTimer))
</script>

<template>
  <div v-if="bootstrap === null" class="loading">正在启动管理服务…</div>
  <main v-else-if="!user" class="login-shell">
    <section class="login-brand"><div class="brand-mark">盘</div><p class="eyebrow">PRIVATE FILE OPERATIONS</p><h1>让分享链接<br><span>真正落地。</span></h1><p>统一索引百度网盘分享目录，按计划同步到本机或 NAS。</p><div class="login-points"><span>目录索引</span><span>安全同步</span><span>权限隔离</span></div></section>
    <form class="login-card" @submit.prevent="login"><div><p class="eyebrow">WELCOME BACK</p><h2>登录管理中心</h2><p>账号由系统管理员在 password.txt 或权限页面维护。</p></div><label>用户名<input v-model.trim="loginForm.username" autocomplete="username" autofocus required placeholder="请输入用户名"></label><label>密码<input v-model="loginForm.password" type="password" autocomplete="current-password" required placeholder="请输入密码"></label><p v-if="loginError" class="error">{{ loginError }}</p><button class="primary wide">进入系统</button><small>默认部署账号请在首次登录后立即修改</small></form>
  </main>

  <div v-else class="app-shell">
    <aside>
      <div class="logo"><div class="brand-mark small">盘</div><div><strong>百度网盘</strong><small>分享管理</small></div></div>
      <nav><button v-for="item in navItems" :key="item[0]" :class="{ active: activeView === item[0] }" @click="switchView(item[0])"><span>{{ item[1] }}</span>{{ item[2] }}</button></nav>
      <div class="account"><div class="avatar">{{ user.username.slice(0, 1).toUpperCase() }}</div><div><strong>{{ user.username }}</strong><small>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</small></div><button title="退出" @click="logout">↗</button></div>
    </aside>
    <section class="workspace">
      <header><div><p class="eyebrow">BAIDU PAN CONTROL</p><h1>{{ currentTitle[0] }}</h1><p>{{ currentTitle[1] }}</p></div><div class="header-status"><i></i>服务运行中</div></header>

      <section v-if="activeView === 'shares'" class="content">
        <div class="toolbar"><div class="stats"><strong>{{ state.links.length }}</strong><span>个分享链接</span></div><button class="primary" @click="openShare()">＋ 添加链接</button></div>
        <div v-if="!state.links.length" class="empty"><b>还没有分享链接</b><span>添加一个百度网盘分享链接，系统会自动建立目录索引。</span></div>
        <div v-else class="item-list"><article v-for="link in state.links" :key="link.id" class="item-card"><div class="file-icon">链</div><div class="grow"><div class="item-title"><h3>{{ link.title || link.url }}</h3><span :class="['pill', link.status]">{{ link.status }}</span></div><p>{{ link.url }}</p><div class="meta"><span>{{ link.file_count }} 个条目</span><span>更新于 {{ formatTime(link.last_checked) }}</span><span v-if="link.note">{{ link.note }}</span></div></div><div class="actions"><button @click="browse(link)">浏览</button><button @click="refreshShare(link.id)">刷新</button><button @click="openShare(link)">编辑</button><button class="danger" @click="deleteShare(link.id)">删除</button></div></article></div>
        <section v-if="browser.open" class="drawer"><div class="drawer-head"><div><p class="eyebrow">DIRECTORY</p><h2>{{ browser.title }}</h2><div class="breadcrumbs"><button @click="browse({ id: browser.linkId, title: browser.title }, '')">根目录</button><template v-for="crumb in crumbs" :key="crumb.path"><span>/</span><button @click="browse({ id: browser.linkId, title: browser.title }, crumb.path)">{{ crumb.name }}</button></template></div></div><button class="close" @click="browser.open = false">×</button></div><table><thead><tr><th>名称</th><th>大小</th><th>修改时间</th></tr></thead><tbody><tr v-for="entry in browser.entries" :key="entry.id"><td><button v-if="entry.is_dir" class="entry" @click="browse({ id: browser.linkId, title: browser.title }, entry.path)">▰ {{ entry.name }}</button><span v-else>▤ {{ entry.name }}</span></td><td>{{ entry.is_dir ? '—' : formatSize(entry.size) }}</td><td>{{ formatTime(entry.modified_time) }}</td></tr><tr v-if="!browser.entries.length"><td colspan="3" class="muted">此目录为空</td></tr></tbody></table></section>
      </section>

      <section v-if="activeView === 'mappings'" class="content"><div class="toolbar"><div class="stats"><strong>{{ state.mappings.length }}</strong><span>条同步映射</span></div><div class="button-row"><button @click="syncAll">同步全部</button><button class="primary" @click="openMapping()">＋ 新建映射</button></div></div><div v-if="!state.mappings.length" class="empty"><b>还没有同步映射</b><span>关联远端目录与本机或 NAS 目录。</span></div><div class="item-list"><article v-for="mapping in state.mappings" :key="mapping.id" class="item-card mapping"><div class="file-icon">映</div><div class="grow"><div class="item-title"><h3>{{ state.links.find(item => item.id === mapping.share_link_id)?.title || '未知分享' }}</h3><span class="pill">{{ mapping.storage_type === 'smb_mount' ? 'NAS · SMB' : '本机目录' }}</span></div><div class="route"><span>{{ mapping.remote_path }}</span><b>→</b><span>{{ mapping.local_path }}</span></div><div class="meta"><span>{{ mapping.sync_strategy }}</span><span>{{ mapping.auto_sync ? `每 ${mapping.schedule_interval} 分钟自动同步` : '手动同步' }}</span><span>上次：{{ formatTime(mapping.last_synced) }}</span></div></div><div class="actions"><button @click="probeStorage(mapping)">检测</button><button class="accent" @click="syncOne(mapping.id)">同步</button><button @click="openMapping(mapping)">编辑</button><button class="danger" @click="deleteMapping(mapping.id)">删除</button></div></article></div></section>

      <section v-if="activeView === 'tasks'" class="content"><div class="two-columns"><div class="panel"><div class="panel-head"><h2>当前任务</h2><span>{{ state.tasks.length }}</span></div><div v-if="!state.tasks.length" class="empty compact">暂无任务</div><div v-for="task in state.tasks" :key="task.id" class="log-row"><span :class="['status-dot', task.status]"></span><div><strong>{{ task.title }}</strong><p>{{ task.message }}</p></div><time>{{ formatTime(task.created_at) }}</time></div></div><div class="panel"><div class="panel-head"><h2>同步历史</h2><span>{{ state.runs.length }}</span></div><div v-if="!state.runs.length" class="empty compact">暂无同步记录</div><div v-for="run in state.runs" :key="run.id" class="log-row"><span :class="['status-dot', run.status]"></span><div><strong>映射 #{{ run.mapping_id }} · {{ run.trigger_type }}</strong><p>{{ run.message }}</p></div><time>{{ formatTime(run.started_at) }}</time></div></div></div></section>

      <section v-if="activeView === 'settings'" class="content"><div class="settings-grid"><div class="panel"><div class="panel-head"><h2>运行配置</h2><span>只读</span></div><dl v-if="settings"><template v-for="(value, key) in settings.config" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl></div><div class="panel"><div class="panel-head"><h2>百度网盘凭据</h2><span :class="['pill', settings?.cookie_configured ? 'active' : '']">{{ settings?.cookie_configured ? '已配置' : '未配置' }}</span></div><p class="muted">Cookie 仅保存在服务端 secrets.json，不会返回浏览器。</p><label>完整 Cookie<textarea v-model="cookieValue" rows="9" placeholder="粘贴包含 BDUSS、STOKEN 等字段的 Cookie"></textarea></label><button class="primary" @click="saveSettings">保存凭据</button></div></div></section>

      <section v-if="activeView === 'users'" class="content"><div class="toolbar"><div class="stats"><strong>{{ users.length }}</strong><span>个系统用户</span></div><button class="primary" @click="openUser()">＋ 添加用户</button></div><div class="panel"><table><thead><tr><th>用户</th><th>角色</th><th>可访问页面</th><th>更新时间</th><th></th></tr></thead><tbody><tr v-for="account in users" :key="account.id"><td><strong>{{ account.username }}</strong></td><td><span class="pill">{{ account.role === 'admin' ? '管理员' : '普通用户' }}</span></td><td><div class="tags"><span v-for="page in account.pages" :key="page">{{ pageMeta[page]?.[0] }}</span></div></td><td>{{ formatTime(account.updated_at) }}</td><td><div class="actions"><button @click="openUser(account)">编辑</button><button class="danger" :disabled="account.id === user.id" @click="deleteUser(account.id)">删除</button></div></td></tr></tbody></table></div></section>
    </section>

    <div v-if="shareModal" class="modal" @click.self="shareModal = false"><form class="modal-card" @submit.prevent="saveShare"><div class="modal-head"><div><p class="eyebrow">SHARE LINK</p><h2>{{ shareForm.id ? '编辑分享链接' : '添加分享链接' }}</h2></div><button type="button" @click="shareModal = false">×</button></div><label>分享链接<input v-model.trim="shareForm.url" required></label><label>提取码<input v-model.trim="shareForm.password" :placeholder="shareForm.id ? '留空表示不修改' : '没有则留空'"></label><label>备注<input v-model.trim="shareForm.note"></label><div class="modal-actions"><button type="button" @click="shareModal = false">取消</button><button class="primary">{{ shareForm.id ? '保存' : '添加并索引' }}</button></div></form></div>
    <div v-if="mappingModal" class="modal" @click.self="mappingModal = false"><form class="modal-card large" @submit.prevent="saveMapping"><div class="modal-head"><div><p class="eyebrow">SYNC MAPPING</p><h2>{{ mappingForm.id ? '编辑同步映射' : '新建同步映射' }}</h2></div><button type="button" @click="mappingModal = false">×</button></div><div class="form-grid"><label>分享链接<select v-model="mappingForm.share_link_id" required><option v-for="link in state.links" :key="link.id" :value="link.id">{{ link.title }}</option></select></label><label>远端目录<input v-model.trim="mappingForm.remote_path" placeholder="/资料/报告" required></label><label>目标存储<select v-model="mappingForm.storage_type"><option value="local">本机目录</option><option value="smb_mount">NAS · SMB 挂载</option></select></label><label>目标路径<input v-model.trim="mappingForm.local_path" :placeholder="mappingForm.storage_type === 'smb_mount' ? '\\\\NAS地址\\共享名\\目录' : 'E:\\Sync 或 /data/sync'" required></label><label>同步策略<select v-model="mappingForm.sync_strategy"><option value="copy_new">仅新增/更新（推荐）</option><option value="mirror">镜像</option><option value="ask">询问</option></select></label><label>自动间隔（分钟）<input v-model.number="mappingForm.schedule_interval" type="number" min="1"></label></div><label class="check"><input v-model="mappingForm.auto_sync" type="checkbox">启用自动同步</label><p v-if="probeMessage" class="probe">{{ probeMessage }}</p><div class="modal-actions"><button type="button" @click="probeStorage()">检测连接</button><button type="button" @click="mappingModal = false">取消</button><button class="primary">保存</button></div></form></div>
    <div v-if="userModal" class="modal" @click.self="userModal = false"><form class="modal-card" @submit.prevent="saveUser"><div class="modal-head"><div><p class="eyebrow">ACCESS CONTROL</p><h2>{{ userForm.id ? '编辑用户' : '添加用户' }}</h2></div><button type="button" @click="userModal = false">×</button></div><label>用户名<input v-model.trim="userForm.username" :disabled="Boolean(userForm.id)" required></label><label>密码<input v-model="userForm.password" type="password" :required="!userForm.id" :placeholder="userForm.id ? '留空表示不修改' : '至少 8 个字符'"></label><label>角色<select v-model="userForm.role"><option value="user">普通用户</option><option value="admin">管理员</option></select></label><fieldset :disabled="userForm.role === 'admin'"><legend>可访问页面</legend><label v-for="page in ['shares','mappings','tasks','settings']" :key="page" class="check"><input type="checkbox" :checked="userForm.pages.includes(page)" @change="togglePage(page)">{{ pageMeta[page][0] }}</label></fieldset><div class="modal-actions"><button type="button" @click="userModal = false">取消</button><button class="primary">保存用户</button></div></form></div>
    <transition name="toast"><div v-if="toastText" class="toast">{{ toastText }}</div></transition>
  </div>
</template>
