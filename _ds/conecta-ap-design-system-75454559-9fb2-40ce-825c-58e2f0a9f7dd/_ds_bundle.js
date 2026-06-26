/* @ds-bundle: {"format":3,"namespace":"ConectaAPDesignSystem_754545","components":[],"sourceHashes":{"ui_kits/conecta-ap/DashboardHome.jsx":"aa65fd59139a","ui_kits/conecta-ap/Header.jsx":"3d16e2530081","ui_kits/conecta-ap/KpiCard.jsx":"7e32943821f0","ui_kits/conecta-ap/LoginScreen.jsx":"b9bc03a3e331","ui_kits/conecta-ap/RmrDetail.jsx":"01a3ab0cc4e1","ui_kits/conecta-ap/Sidebar.jsx":"c6df08395e85"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.ConectaAPDesignSystem_754545 = window.ConectaAPDesignSystem_754545 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// ui_kits/conecta-ap/DashboardHome.jsx
try { (() => {
// DashboardHome.jsx
const QUICK = [{
  icon: 'event_note',
  label: 'Nova RMR'
}, {
  icon: 'person_add',
  label: 'Novo Cliente'
}, {
  icon: 'assessment',
  label: 'Relatórios'
}, {
  icon: 'verified',
  label: 'Selo EAP'
}, {
  icon: 'poll',
  label: 'Pesquisa'
}, {
  icon: 'business',
  label: 'Empresas'
}, {
  icon: 'trending_up',
  label: 'Indicadores'
}, {
  icon: 'account_tree',
  label: 'Organograma'
}];
const CLIENTES = [{
  initials: 'MC',
  name: 'Magna Contábil',
  meta: 'Pilar 3 · 4 indicadores ok',
  chip: 'Selo EAP',
  val: '92%'
}, {
  initials: 'TS',
  name: 'Tecsul Soluções',
  meta: 'Pilar 1 · 2 indicadores ok',
  chip: 'Premium',
  val: '84%'
}, {
  initials: 'VP',
  name: 'Viana Participações',
  meta: 'Pilar 5 · 3 indicadores ok',
  chip: 'Standard',
  val: '76%'
}, {
  initials: 'OL',
  name: 'Oliveira Logística',
  meta: 'Pilar 2 · 1 indicador ok',
  chip: 'Standard',
  val: '68%'
}];
function DashboardHome({
  onOpenRmr
}) {
  const h = new Date().getHours();
  const greet = h < 12 ? 'Bom dia' : h < 18 ? 'Boa tarde' : 'Boa noite';
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "page-head"
  }, /*#__PURE__*/React.createElement("h1", null, greet, ", Gabriel!"), /*#__PURE__*/React.createElement("p", null, "Aqui est\xE1 o panorama da sua consultoria neste ciclo.")), /*#__PURE__*/React.createElement("div", {
    className: "kpi-grid"
  }, /*#__PURE__*/React.createElement(KpiCard, {
    icon: "groups",
    label: "Clientes Ativos",
    value: "247",
    color: "gold",
    trend: "+12 este m\xEAs",
    trendDir: "up"
  }), /*#__PURE__*/React.createElement(KpiCard, {
    icon: "trending_up",
    label: "Meta EAP",
    value: "87",
    suffix: "%",
    color: "blue",
    trend: "+4,2 p.p.",
    trendDir: "up"
  }), /*#__PURE__*/React.createElement(KpiCard, {
    icon: "event_available",
    label: "RMR no M\xEAs",
    value: "34",
    color: "purple",
    trend: "\u22122 vs. anterior",
    trendDir: "dn"
  }), /*#__PURE__*/React.createElement(KpiCard, {
    icon: "verified",
    label: "Selos Concedidos",
    value: "18",
    color: "green",
    trend: "+3 este ciclo",
    trendDir: "up"
  })), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Acesso R\xE1pido",
    subtitle: "Fixe at\xE9 6 atalhos no seu dashboard"
  }, /*#__PURE__*/React.createElement("div", {
    className: "qa-grid"
  }, QUICK.map(q => /*#__PURE__*/React.createElement("button", {
    key: q.label,
    className: "qa",
    onClick: () => q.label === 'Nova RMR' && onOpenRmr()
  }, /*#__PURE__*/React.createElement("div", {
    className: "ic"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, q.icon)), /*#__PURE__*/React.createElement("span", {
    className: "nm"
  }, q.label))))), /*#__PURE__*/React.createElement("div", {
    style: {
      height: 18
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "two-col"
  }, /*#__PURE__*/React.createElement(SectionCard, {
    title: "Clientes Gerenciados pelo EAP",
    subtitle: "Top performance no ciclo atual",
    action: "Ver todos"
  }, CLIENTES.map(c => /*#__PURE__*/React.createElement("div", {
    key: c.initials,
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "av"
  }, c.initials), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "nm"
  }, c.name), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, c.meta)), /*#__PURE__*/React.createElement("span", {
    className: "chip"
  }, c.chip), /*#__PURE__*/React.createElement("span", {
    className: "val"
  }, c.val)))), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Pr\xF3ximas Reuni\xF5es",
    subtitle: "Agenda dos pr\xF3ximos 7 dias",
    action: "Abrir agenda"
  }, [{
    dia: '18',
    mes: 'ABR',
    nm: 'RMR — Magna Contábil',
    hora: '09:00 · Sala virtual',
    tag: 'RMR'
  }, {
    dia: '19',
    mes: 'ABR',
    nm: 'RSC — Tecsul Soluções',
    hora: '14:30 · Presencial',
    tag: 'RSC'
  }, {
    dia: '21',
    mes: 'ABR',
    nm: 'RMR — Viana Participações',
    hora: '10:00 · Sala virtual',
    tag: 'RMR'
  }].map((e, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "row",
    style: {
      gridTemplateColumns: '48px 1fr auto'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 48,
      height: 48,
      borderRadius: 12,
      background: 'var(--surface-light)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      border: '1px solid rgba(199,164,68,0.2)'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-primary)',
      fontWeight: 700,
      fontSize: 16,
      color: 'var(--primary)',
      lineHeight: 1
    }
  }, e.dia), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-primary)',
      fontWeight: 600,
      fontSize: 9,
      color: 'var(--text-muted)',
      letterSpacing: '0.08em'
    }
  }, e.mes)), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "nm"
  }, e.nm), /*#__PURE__*/React.createElement("div", {
    className: "sub"
  }, e.hora)), /*#__PURE__*/React.createElement("span", {
    className: "chip"
  }, e.tag))))));
}
window.DashboardHome = DashboardHome;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/conecta-ap/DashboardHome.jsx", error: String((e && e.message) || e) }); }

// ui_kits/conecta-ap/Header.jsx
try { (() => {
// Header.jsx — fixed 64px top bar
function Header({
  title
}) {
  return /*#__PURE__*/React.createElement("header", {
    className: "hd"
  }, /*#__PURE__*/React.createElement("h2", {
    className: "hd-title"
  }, title), /*#__PURE__*/React.createElement("div", {
    className: "hd-search"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon",
    style: {
      fontSize: 20
    }
  }, "search"), /*#__PURE__*/React.createElement("input", {
    placeholder: "Buscar cliente, consultor, indicador\u2026"
  })), /*#__PURE__*/React.createElement("div", {
    className: "hd-right"
  }, /*#__PURE__*/React.createElement("button", {
    className: "icon-btn",
    title: "Notifica\xE7\xF5es"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, "notifications"), /*#__PURE__*/React.createElement("span", {
    className: "dot"
  })), /*#__PURE__*/React.createElement("button", {
    className: "icon-btn",
    title: "Ajuda"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, "help")), /*#__PURE__*/React.createElement("div", {
    className: "profile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "av"
  }, "GA"), /*#__PURE__*/React.createElement("div", {
    className: "info"
  }, /*#__PURE__*/React.createElement("span", {
    className: "nm"
  }, "Gabriel Alvarez"), /*#__PURE__*/React.createElement("span", {
    className: "rl"
  }, "Coordenador")), /*#__PURE__*/React.createElement("span", {
    className: "ms-icon",
    style: {
      fontSize: 18,
      color: 'var(--text-muted)'
    }
  }, "expand_more"))));
}
window.Header = Header;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/conecta-ap/Header.jsx", error: String((e && e.message) || e) }); }

// ui_kits/conecta-ap/KpiCard.jsx
try { (() => {
// KpiCard.jsx
function KpiCard({
  icon,
  label,
  value,
  suffix,
  trend,
  trendDir,
  color
}) {
  const colorMap = {
    gold: {
      bg: 'rgba(199,164,68,0.12)',
      fg: '#E8B84B',
      orb: '#C7A444'
    },
    blue: {
      bg: 'rgba(33,150,243,0.12)',
      fg: '#64B5F6',
      orb: '#2196F3'
    },
    green: {
      bg: 'rgba(76,175,80,0.12)',
      fg: '#81C784',
      orb: '#4CAF50'
    },
    purple: {
      bg: 'rgba(124,58,237,0.12)',
      fg: '#A78BFA',
      orb: '#7C3AED'
    }
  };
  const c = colorMap[color] || colorMap.gold;
  return /*#__PURE__*/React.createElement("div", {
    className: "kpi"
  }, /*#__PURE__*/React.createElement("div", {
    className: "orb",
    style: {
      background: c.orb
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "kpi-icn",
    style: {
      background: c.bg,
      color: c.fg
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, icon)), /*#__PURE__*/React.createElement("div", {
    className: "kpi-label"
  }, label), /*#__PURE__*/React.createElement("div", {
    className: "kpi-val"
  }, value, suffix && /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 20,
      color: 'var(--text-muted)',
      fontWeight: 600
    }
  }, suffix)), trend && /*#__PURE__*/React.createElement("div", {
    className: `kpi-trend ${trendDir}`
  }, trendDir === 'up' ? '▲' : '▼', " ", trend));
}
function SectionCard({
  title,
  subtitle,
  action,
  onAction,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "section"
  }, /*#__PURE__*/React.createElement("div", {
    className: "section-hd"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "t"
  }, title), subtitle && /*#__PURE__*/React.createElement("div", {
    className: "s"
  }, subtitle)), action && /*#__PURE__*/React.createElement("button", {
    className: "link-action",
    onClick: onAction
  }, action, " \u2192")), children);
}
window.KpiCard = KpiCard;
window.SectionCard = SectionCard;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/conecta-ap/KpiCard.jsx", error: String((e && e.message) || e) }); }

// ui_kits/conecta-ap/LoginScreen.jsx
try { (() => {
// LoginScreen.jsx — 20px radius card with gold blobs + parallax
function LoginScreen({
  onLogin
}) {
  const [email, setEmail] = React.useState('gabriel@sejaap.com.br');
  const [pass, setPass] = React.useState('••••••••');
  const [offset, setOffset] = React.useState({
    x: 0,
    y: 0
  });
  React.useEffect(() => {
    const onMove = e => {
      const cx = window.innerWidth / 2,
        cy = window.innerHeight / 2;
      setOffset({
        x: (e.clientX - cx) / 40,
        y: (e.clientY - cy) / 40
      });
    };
    window.addEventListener('mousemove', onMove);
    return () => window.removeEventListener('mousemove', onMove);
  }, []);
  const submit = e => {
    e.preventDefault();
    onLogin();
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "login-stage"
  }, /*#__PURE__*/React.createElement("div", {
    className: "blob a",
    style: {
      transform: `translate(${offset.x * 2}px, ${offset.y * 2}px)`
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "blob b",
    style: {
      transform: `translate(${-offset.x * 1.5}px, ${-offset.y * 1.5}px)`
    }
  }), /*#__PURE__*/React.createElement("form", {
    className: "login-card",
    onSubmit: submit
  }, /*#__PURE__*/React.createElement("div", {
    className: "login-logo"
  }, /*#__PURE__*/React.createElement("img", {
    src: "../../assets/LOGO-SEJAAP-SLOGAN-NEGATIVO.svg",
    alt: "SEJA AP"
  })), /*#__PURE__*/React.createElement("h2", null, "Bem-vindo de volta"), /*#__PURE__*/React.createElement("p", {
    className: "sub"
  }, "Acesse sua plataforma de evolu\xE7\xE3o empresarial"), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "E-mail"), /*#__PURE__*/React.createElement("div", {
    className: "input"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, "mail"), /*#__PURE__*/React.createElement("input", {
    type: "email",
    value: email,
    onChange: e => setEmail(e.target.value),
    placeholder: "seu@email.com"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Senha"), /*#__PURE__*/React.createElement("div", {
    className: "input"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, "lock"), /*#__PURE__*/React.createElement("input", {
    type: "password",
    value: pass,
    onChange: e => setPass(e.target.value),
    placeholder: "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
  }))), /*#__PURE__*/React.createElement("button", {
    type: "submit",
    className: "btn-submit"
  }, "ENTRAR"), /*#__PURE__*/React.createElement("div", {
    className: "login-links"
  }, /*#__PURE__*/React.createElement("a", {
    href: "#"
  }, "Esqueceu a senha?"), /*#__PURE__*/React.createElement("span", {
    className: "login-sep"
  }, "\xB7"), /*#__PURE__*/React.createElement("a", {
    href: "#"
  }, "Precisa de ajuda?"))));
}
window.LoginScreen = LoginScreen;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/conecta-ap/LoginScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/conecta-ap/RmrDetail.jsx
try { (() => {
// RmrDetail.jsx — Reunião Mensal de Resultados
const PILARES = [{
  n: 1,
  l: 'Financeiro'
}, {
  n: 2,
  l: 'Processos'
}, {
  n: 3,
  l: 'Pessoas'
}, {
  n: 4,
  l: 'Cliente'
}, {
  n: 5,
  l: 'Mercado'
}, {
  n: 6,
  l: 'Gestão'
}, {
  n: 7,
  l: 'Estratégia'
}];
const INDICADORES = [{
  nm: 'Margem Líquida',
  sub: 'Meta: 18% · Realizado: 17,4%',
  pct: 97,
  st: 'ok'
}, {
  nm: 'Ticket Médio',
  sub: 'Meta: R$ 4.200 · Real: R$ 4.510',
  pct: 107,
  st: 'ok'
}, {
  nm: 'Inadimplência',
  sub: 'Meta: < 4% · Realizado: 5,2%',
  pct: 62,
  st: 'wn'
}, {
  nm: 'Custo de Aquisição',
  sub: 'Meta: R$ 280 · Real: R$ 340',
  pct: 45,
  st: 'er'
}, {
  nm: 'Retenção de Clientes',
  sub: 'Meta: 85% · Realizado: 88%',
  pct: 103,
  st: 'ok'
}];
function RmrDetail({
  onBack
}) {
  const [pillar, setPillar] = React.useState(1);
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "rmr-hd"
  }, /*#__PURE__*/React.createElement("button", {
    className: "back-btn",
    onClick: onBack
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, "arrow_back")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: 0,
      fontFamily: 'var(--font-primary)',
      fontWeight: 700,
      fontSize: 24,
      color: 'var(--text)',
      letterSpacing: '-0.01em'
    }
  }, "RMR \u2014 Magna Cont\xE1bil"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: '4px 0 0',
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Ciclo Abril/2026 \xB7 18 de Abril \xB7 09:00 \xB7 Sala virtual")), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "icon-btn",
    title: "Exportar"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, "download")), /*#__PURE__*/React.createElement("button", {
    style: {
      background: 'var(--gradient-primary)',
      color: '#1A1A18',
      border: 'none',
      borderRadius: 8,
      padding: '0 16px',
      height: 40,
      fontFamily: 'var(--font-primary)',
      fontWeight: 600,
      fontSize: 13,
      letterSpacing: '0.04em',
      cursor: 'pointer'
    }
  }, "SALVAR RMR"))), /*#__PURE__*/React.createElement("div", {
    className: "pillar-grid"
  }, PILARES.map(p => /*#__PURE__*/React.createElement("div", {
    key: p.n,
    className: `pillar ${pillar === p.n ? 'on' : ''}`,
    onClick: () => setPillar(p.n)
  }, /*#__PURE__*/React.createElement("div", {
    className: "n"
  }, p.n), /*#__PURE__*/React.createElement("div", {
    className: "l"
  }, p.l)))), /*#__PURE__*/React.createElement(SectionCard, {
    title: `Pilar ${pillar} · ${PILARES[pillar - 1].l}`,
    subtitle: "Indicadores monitorados neste ciclo",
    action: "Adicionar indicador"
  }, INDICADORES.map((i, idx) => /*#__PURE__*/React.createElement("div", {
    key: idx,
    className: "indic-row"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "indic-nm"
  }, i.nm), /*#__PURE__*/React.createElement("div", {
    className: "indic-sub"
  }, i.sub)), /*#__PURE__*/React.createElement("div", {
    className: "progress-bar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "progress-fill",
    style: {
      width: Math.min(i.pct, 100) + '%',
      background: i.st === 'ok' ? 'linear-gradient(90deg, #4ADE80, #28A745)' : i.st === 'wn' ? 'linear-gradient(90deg, #FFC107, #F59E0B)' : 'linear-gradient(90deg, #F87171, #DC3545)'
    }
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: 13,
      color: 'var(--text)',
      fontWeight: 600,
      minWidth: 48,
      textAlign: 'right'
    }
  }, i.pct, "%"), /*#__PURE__*/React.createElement("span", {
    className: `status-chip ${i.st === 'ok' ? 'status-ok' : i.st === 'wn' ? 'status-wn' : 'status-er'}`
  }, i.st === 'ok' ? 'Atingido' : i.st === 'wn' ? 'Parcial' : 'Abaixo')))));
}
window.RmrDetail = RmrDetail;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/conecta-ap/RmrDetail.jsx", error: String((e && e.message) || e) }); }

// ui_kits/conecta-ap/Sidebar.jsx
try { (() => {
// Sidebar.jsx — 72 ↔ 260px collapsible nav
const NAV = [{
  group: 'Principal',
  items: [{
    id: 'visao-geral',
    icon: 'dashboard',
    label: 'Visão Geral'
  }, {
    id: 'meu-dashboard',
    icon: 'space_dashboard',
    label: 'Meu Dashboard'
  }]
}, {
  group: 'Gestão',
  items: [{
    id: 'clientes',
    icon: 'groups',
    label: 'Clientes',
    cnt: '247'
  }, {
    id: 'consultores',
    icon: 'manage_accounts',
    label: 'Consultores',
    cnt: '34'
  }, {
    id: 'rmr',
    icon: 'event_note',
    label: 'Reuniões (RMR)'
  }, {
    id: 'indicadores',
    icon: 'trending_up',
    label: 'Indicadores'
  }]
}, {
  group: 'Selo & Pesquisas',
  items: [{
    id: 'selo',
    icon: 'verified',
    label: 'Selo EAP'
  }, {
    id: 'pesquisa',
    icon: 'poll',
    label: 'Pesquisas'
  }]
}, {
  group: 'Sistema',
  items: [{
    id: 'organograma',
    icon: 'account_tree',
    label: 'Organograma'
  }, {
    id: 'config',
    icon: 'settings',
    label: 'Configurações'
  }]
}];
function Sidebar({
  active,
  onNav,
  expanded,
  onToggle
}) {
  return /*#__PURE__*/React.createElement("aside", {
    className: "sb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "sb-brand"
  }, /*#__PURE__*/React.createElement("img", {
    src: "../../assets/LOGO-SEJAAP-NEGATIVO.svg",
    alt: "SEJA AP"
  }), /*#__PURE__*/React.createElement("button", {
    className: "sb-toggle",
    onClick: onToggle,
    title: expanded ? 'Recolher' : 'Expandir'
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon",
    style: {
      fontSize: 20
    }
  }, expanded ? 'chevron_left' : 'chevron_right'))), /*#__PURE__*/React.createElement("nav", {
    className: "sb-nav"
  }, NAV.map(grp => /*#__PURE__*/React.createElement(React.Fragment, {
    key: grp.group
  }, /*#__PURE__*/React.createElement("div", {
    className: "sb-group sb-label"
  }, grp.group), grp.items.map(item => /*#__PURE__*/React.createElement("button", {
    key: item.id,
    className: `sb-item ${active === item.id ? 'active' : ''}`,
    onClick: () => onNav(item.id),
    title: item.label
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, item.icon), /*#__PURE__*/React.createElement("span", {
    className: "sb-label"
  }, item.label), item.cnt && /*#__PURE__*/React.createElement("span", {
    className: "cnt"
  }, item.cnt)))))), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 12,
      borderTop: '1px solid var(--border-light)'
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "sb-item",
    onClick: () => onNav('logout')
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-icon"
  }, "logout"), /*#__PURE__*/React.createElement("span", {
    className: "sb-label"
  }, "Sair"))));
}
window.Sidebar = Sidebar;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/conecta-ap/Sidebar.jsx", error: String((e && e.message) || e) }); }

})();
