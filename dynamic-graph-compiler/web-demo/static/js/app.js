/**
 * DynamicAnythingMachine — LangGraph Observatory Frontend
 *
 * Handles SSE streaming from /execute, node visualization,
 * audit trail updates, and progressive output display.
 * Polished UX with loading states, progress tracking, and smooth animations.
 */

(function () {
    'use strict';

    // ---------------------------------------------------------------------------
    // State
    // ---------------------------------------------------------------------------
    const state = {
        currentRunId: null,
        isRunning: false,
        mode: 'research',
        activeAgents: new Set(),
        completedAgents: new Set(),
        nodes: {},
        edges: [],
        phase: 0,
        auditEvents: [],
        streamingTokens: [],
        finalOutput: '',
        startTime: null,
        estimatedTimeRemaining: null,
        totalPhases: 3,
    };

    // ---------------------------------------------------------------------------
    // DOM References
    // ---------------------------------------------------------------------------
    const $ = (id) => document.getElementById(id);
    const goalTextarea    = $('goalTextarea');
    const runBtn          = $('runBtn');
    const runBtnText      = $('runBtnText');
    const statusDot       = $('statusDot');
    const statusText      = $('statusText');
    const runInfo         = $('runInfo');
    const runIdDisplay    = $('runIdDisplay');
    const phaseDisplay    = $('phaseDisplay');
    const activeNodesDisplay = $('activeNodesDisplay');
    const completedNodesDisplay = $('completedNodesDisplay');
    const progressDisplay = $('progressDisplay');
    const etaDisplay      = $('etaDisplay');
    const mainArea        = $('mainArea');
    const emptyState      = $('emptyState');
    const runContent      = $('runContent');
    const loadingSkeleton = $('loadingSkeleton');
    const graphPanel      = $('graphPanel');
    const graphCanvas     = $('graphCanvas');
    const edgeLayer       = $('edgeLayer');
    const outputTitle     = $('outputTitle');
    const outputContent   = $('outputContent');
    const outputMeta      = $('outputMeta');
    const copyOutputBtn   = $('copyOutputBtn');
    const finalOutputSection = $('finalOutputSection');
    const finalOutputDoc = $('finalOutputDoc');
    const copyFinalBtn    = $('copyFinalBtn');
    const downloadAuditBtn = $('downloadAuditBtn');
    const downloadArtifactsBtn = $('downloadArtifactsBtn');
    const auditTimeline   = $('auditTimeline');
    const auditCount      = $('auditCount');
    const reportMeta      = $('reportMeta');

    // Phase elements
    const phase1Step = $('phase1Step');
    const phase2Step = $('phase2Step');
    const phase3Step = $('phase3Step');
    const connector1 = $('connector1');
    const connector2 = $('connector2');

    // ---------------------------------------------------------------------------
    // Utility
    // ---------------------------------------------------------------------------
    function formatTime(date) {
        return date.toTimeString().slice(0, 8);
    }

    function formatDuration(ms) {
        if (ms < 0) return '--';
        const seconds = Math.floor(ms / 1000);
        if (seconds < 60) return `${seconds}s`;
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        return `${minutes}m ${remainingSeconds}s`;
    }

    function updateETA() {
        if (!state.startTime || !state.estimatedTimeRemaining) {
            etaDisplay.textContent = '--';
            return;
        }
        const elapsed = Date.now() - state.startTime;
        const remaining = Math.max(0, state.estimatedTimeRemaining - elapsed);
        etaDisplay.textContent = formatDuration(remaining);
    }

    function addAuditEvent(type, detail) {
        const time = formatTime(new Date());
        state.auditEvents.push({ type, detail, time });

        const event = document.createElement('div');
        event.className = `audit-event ${type}`;
        event.innerHTML = `
            <span class="audit-time">${time}</span>
            <span class="audit-type">${type.replace(/_/g, ' ')}</span>
            <span class="audit-detail">${detail}</span>
        `;
        auditTimeline.appendChild(event);
        auditTimeline.scrollTop = auditTimeline.scrollHeight;

        auditCount.textContent = state.auditEvents.length;
    }

    function updatePhase(phaseNum) {
        state.phase = phaseNum;

        // Reset all
        [phase1Step, phase2Step, phase3Step].forEach(el => {
            el.classList.remove('active', 'completed');
        });
        [connector1, connector2].forEach(el => {
            el.classList.remove('active');
        });

        if (phaseNum >= 1) phase1Step.classList.add('completed');
        if (phaseNum >= 2) {
            phase1Step.classList.add('completed');
            connector1.classList.add('active');
        }
        if (phaseNum >= 3) {
            phase2Step.classList.add('completed');
            connector2.classList.add('active');
        }

        if (phaseNum === 1) phase1Step.classList.add('active');
        if (phaseNum === 2) phase2Step.classList.add('active');
        if (phaseNum === 3) phase3Step.classList.add('active');

        const labels = ['', 'Analysis', 'Execution', 'Synthesis'];
        phaseDisplay.textContent = labels[phaseNum] || '-';

        // Update progress percentage
        updateProgress();
    }

    function updateProgress() {
        const totalNodes = Object.keys(state.nodes).length || 1;
        const completed = state.completedAgents.size;
        const active = state.activeAgents.size;
        const phaseProgress = (state.phase / state.totalPhases) * 100;
        const nodeProgress = totalNodes > 0 ? (completed / totalNodes) * 100 : 0;
        const overallProgress = Math.min(99, Math.round(phaseProgress * 0.4 + nodeProgress * 0.6));
        progressDisplay.textContent = `${overallProgress}%`;
    }

    function updateStatus(text, active = false) {
        statusText.textContent = text;
        if (active) {
            statusDot.classList.add('active');
        } else {
            statusDot.classList.remove('active');
        }
    }

    function setButtonsDisabled(disabled) {
        document.querySelectorAll('.preset-chip, .mode-btn').forEach(btn => {
            btn.disabled = disabled;
        });
        runBtn.disabled = disabled;
    }

    function smoothScrollToBottom() {
        requestAnimationFrame(() => {
            outputContent.scrollTop = outputContent.scrollHeight;
        });
    }

    // ---------------------------------------------------------------------------
    // Node Graph Visualization
    // ---------------------------------------------------------------------------

    const NODE_POSITIONS = {
        orchestrator: { row: 0, col: 2, label: 'Orchestrator' },
        router:       { row: 0, col: 1, label: 'Router' },
        synthesis:    { row: 0, col: 3, label: 'Synthesis' },
        human_gate:   { row: 0, col: 4, label: 'HumanGate' },
    };

    function getNodeType(agentId) {
        if (agentId === 'orchestrator' || agentId === 'router' || agentId === 'synthesis' || agentId === 'human_gate') {
            return agentId;
        }
        if (agentId.startsWith('manager_')) return 'manager';
        if (agentId.startsWith('worker_')) return 'worker';
        return 'unknown';
    }

    function createNodeCard(agentId, nodeType, label) {
        const card = document.createElement('div');
        card.className = `node-card ${nodeType} pending`;
        card.id = `node-${agentId}`;
        card.innerHTML = `
            <div class="node-name">${label || agentId}</div>
            <div class="node-type">${nodeType}</div>
            <div class="node-status-text">pending</div>
        `;
        return card;
    }

    function positionNodes() {
        const canvasRect = graphCanvas.getBoundingClientRect();
        const nodeWidth = 130;
        const nodeHeight = 60;
        const rowGap = 90;
        const colGap = 160;

        const positions = {};
        const maxCols = Math.max(
            ...Object.values(NODE_POSITIONS).map(p => p.col),
            ...Object.values(state.nodes).map(n => n.col || 0)
        );
        const totalWidth = (maxCols + 1) * colGap;
        const startX = Math.max(20, (canvasRect.width - totalWidth) / 2);

        const corePositions = {
            orchestrator: { x: startX + 2 * colGap, y: 30 + 0 * rowGap },
            router:       { x: startX + 1 * colGap, y: 30 + 0 * rowGap },
            synthesis:    { x: startX + 3 * colGap, y: 30 + 0 * rowGap },
            human_gate:   { x: startX + 4 * colGap, y: 30 + 0 * rowGap },
        };

        const managers = Object.entries(state.nodes).filter(([id]) => id.startsWith('manager_'));
        const workers = Object.entries(state.nodes).filter(([id]) => id.startsWith('worker_'));

        managers.forEach(([id, info], i) => {
            positions[id] = { x: startX + (2 + i) * colGap, y: 30 + 1 * rowGap };
            info.col = 2 + i;
        });

        workers.forEach(([id, info], i) => {
            const mgrIndex = info.managerIndex || 0;
            positions[id] = { x: startX + (2 + mgrIndex) * colGap, y: 30 + 2 * rowGap };
            info.col = 2 + mgrIndex;
        });

        Object.entries(state.nodes).forEach(([id, info]) => {
            const pos = positions[id] || { x: startX + 2 * colGap, y: 30 + 3 * rowGap };
            if (info.element) {
                info.element.style.left = `${pos.x}px`;
                info.element.style.top = `${pos.y}px`;
            }
        });

        updateEdgeLayer();
    }

    function addNode(agentId, nodeType) {
        if (state.nodes[agentId]) return;

        const label = NODE_POSITIONS[agentId]?.label || agentId;
        const element = createNodeCard(agentId, nodeType, label);
        graphCanvas.appendChild(element);

        state.nodes[agentId] = {
            type: nodeType,
            status: 'pending',
            element,
            col: NODE_POSITIONS[agentId]?.col,
        };

        positionNodes();
        updateProgress();
    }

    function setNodeStatus(agentId, status, statusText) {
        const node = state.nodes[agentId];
        if (!node) return;

        node.status = status;
        node.element.classList.remove('pending', 'active', 'completed', 'failed');
        node.element.classList.add(status);

        const statusEl = node.element.querySelector('.node-status-text');
        if (statusEl) statusEl.textContent = statusText || status;
    }

    function updateEdgeLayer() {
        edgeLayer.innerHTML = '';

        const edges = [];

        if (state.nodes['router'] && state.nodes['orchestrator']) {
            edges.push(['router', 'orchestrator']);
        }
        if (state.nodes['orchestrator']) {
            Object.keys(state.nodes).filter(id => id.startsWith('manager_')).forEach(mid => {
                edges.push(['orchestrator', mid]);
            });
        }
        Object.entries(state.nodes).filter(([id]) => id.startsWith('manager_')).forEach(([mid]) => {
            Object.entries(state.nodes).filter(([id]) => id.startsWith('worker_')).forEach(([wid, winfo]) => {
                if (winfo.managerId === mid) {
                    edges.push([mid, wid]);
                }
            });
        });

        if (state.nodes['synthesis']) {
            Object.keys(state.nodes).filter(id => id.startsWith('manager_')).forEach(mid => {
                edges.push([mid, 'synthesis']);
            });
        }

        const svgNS = 'http://www.w3.org/2000/svg';
        edges.forEach(([fromId, toId]) => {
            const fromNode = state.nodes[fromId];
            const toNode = state.nodes[toId];
            if (!fromNode || !toNode) return;

            const fromEl = fromNode.element;
            const toEl = toNode.element;

            const fx = parseFloat(fromEl.style.left) + fromEl.offsetWidth / 2;
            const fy = parseFloat(fromEl.style.top) + fromEl.offsetHeight / 2;
            const tx = parseFloat(toEl.style.left) + toEl.offsetWidth / 2;
            const ty = parseFloat(toEl.style.top) + toEl.offsetHeight / 2;

            const line = document.createElementNS(svgNS, 'line');
            line.setAttribute('x1', fx);
            line.setAttribute('y1', fy);
            line.setAttribute('x2', tx);
            line.setAttribute('y2', ty);
            line.classList.add('edge-line');

            if (fromNode.status === 'completed' && toNode.status === 'completed') {
                line.classList.add('completed');
            } else if (fromNode.status === 'active' || toNode.status === 'active') {
                line.classList.add('active');
            }

            edgeLayer.appendChild(line);
        });
    }

    function clearGraph() {
        Object.values(state.nodes).forEach(n => n.element?.remove());
        state.nodes = {};
        edgeLayer.innerHTML = '';
    }

    // ---------------------------------------------------------------------------
    // Output Streaming
    // ---------------------------------------------------------------------------

    function appendOutputToken(token) {
        const empty = outputContent.querySelector('.output-empty');
        if (empty) empty.remove();

        const span = document.createElement('span');
        span.className = 'output-token';
        span.textContent = token;
        outputContent.appendChild(span);

        smoothScrollToBottom();
    }

    function appendStreamingTokens(tokens) {
        tokens.forEach(t => {
            if (typeof t === 'string') {
                appendOutputToken(t);
            } else if (t.node_tag) {
                const tag = document.createElement('span');
                tag.className = 'output-node-tag';
                tag.textContent = t.node_tag;
                outputContent.appendChild(tag);
            } else if (t.bracket) {
                const bracket = document.createElement('span');
                bracket.className = 'output-bracket';
                bracket.textContent = t.bracket;
                outputContent.appendChild(bracket);
            }
        });

        state.streamingTokens = state.streamingTokens.concat(tokens);
        copyOutputBtn.disabled = false;

        // Update output meta
        const tokenCount = state.streamingTokens.length;
        outputMeta.textContent = `${tokenCount} tokens`;
    }

    function displayFinalOutput(outputText) {
        finalOutputSection.style.display = 'flex';

        // Try to parse as structured report
        let isDocument = false;
        let docContent = '';

        try {
            const parsed = JSON.parse(outputText);
            if (parsed && typeof parsed === 'object') {
                isDocument = true;
                docContent = renderStructuredDocument(parsed);
            }
        } catch {
            // Not JSON, try to detect if it's markdown-like
            if (outputText.includes('# ') || outputText.includes('## ') ||
                outputText.includes('**') || outputText.includes('```')) {
                isDocument = true;
                docContent = renderMarkdownDocument(outputText);
            }
        }

        if (isDocument && docContent) {
            finalOutputDoc.innerHTML = docContent;
            reportMeta.textContent = 'Structured Report';
        } else {
            // Fallback to syntax highlighted code
            finalOutputDoc.innerHTML = `<pre class="final-output-code">${highlightSyntax(escapeHtml(outputText))}</pre>`;
            reportMeta.textContent = `${outputText.length} characters`;
        }

        state.finalOutput = outputText;
        copyFinalBtn.disabled = false;
        downloadAuditBtn.disabled = false;
    }

    function renderStructuredDocument(data) {
        let html = '';

        if (data.title || data.heading) {
            html += `<h1>${escapeHtml(data.title || data.heading)}</h1>`;
        }

        if (data.summary || data.executive_summary) {
            html += `<blockquote>${escapeHtml(data.summary || data.executive_summary)}</blockquote>`;
        }

        if (data.sections && Array.isArray(data.sections)) {
            data.sections.forEach(section => {
                if (section.heading) {
                    html += `<h2>${escapeHtml(section.heading)}</h2>`;
                }
                if (section.content) {
                    html += `<p>${escapeHtml(section.content)}</p>`;
                }
                if (section.items && Array.isArray(section.items)) {
                    html += '<ul>';
                    section.items.forEach(item => {
                        html += `<li>${escapeHtml(item)}</li>`;
                    });
                    html += '</ul>';
                }
            });
        } else if (data.findings && Array.isArray(data.findings)) {
            html += '<h2>Key Findings</h2><ul>';
            data.findings.forEach(finding => {
                if (typeof finding === 'string') {
                    html += `<li>${escapeHtml(finding)}</li>`;
                } else if (finding.description) {
                    html += `<li><strong>${escapeHtml(finding.title || '')}</strong>: ${escapeHtml(finding.description)}</li>`;
                }
            });
            html += '</ul>';
        } else if (data.key_insights) {
            html += '<h2>Key Insights</h2><ul>';
            Object.entries(data.key_insights).forEach(([key, value]) => {
                html += `<li><strong>${escapeHtml(key)}</strong>: ${escapeHtml(String(value))}</li>`;
            });
            html += '</ul>';
        }

        // Render any remaining top-level string fields as paragraphs
        const skipKeys = ['title', 'heading', 'summary', 'executive_summary', 'sections', 'findings', 'key_insights'];
        Object.entries(data).forEach(([key, value]) => {
            if (skipKeys.includes(key)) return;
            if (typeof value === 'string' && value.length < 500) {
                html += `<p><strong>${escapeHtml(key)}:</strong> ${escapeHtml(value)}</p>`;
            }
        });

        return html || `<pre class="final-output-code">${highlightSyntax(escapeHtml(JSON.stringify(data, null, 2)))}</pre>`;
    }

    function renderMarkdownDocument(text) {
        // Simple markdown rendering
        let html = escapeHtml(text);

        // Headers
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

        // Bold
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Code blocks
        html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Line breaks
        html = html.replace(/\n\n/g, '</p><p>');
        html = `<p>${html}</p>`;

        // Clean up empty paragraphs
        html = html.replace(/<p><\/p>/g, '');
        html = html.replace(/<p>(<h[1-3]>)/g, '$1');
        html = html.replace(/(<\/h[1-3]>)<\/p>/g, '$1');

        return html;
    }

    function escapeHtml(text) {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function highlightSyntax(text) {
        // Strings (double quoted)
        text = text.replace(/"([^"\\]|\\.)*"/g, (match) => {
            return `<span class="syntax-string">${match}</span>`;
        });
        // Keys (quoted followed by colon)
        text = text.replace(/"([^"]+)":/g, (match, key) => {
            return `<span class="syntax-key">"${key}"</span>:`;
        });
        // Booleans
        text = text.replace(/\b(true|false)\b/g, '<span class="syntax-bool">$1</span>');
        // Null
        text = text.replace(/\bnull\b/g, '<span class="syntax-null">null</span>');
        // Numbers
        text = text.replace(/\b(\d+\.?\d*)\b/g, '<span class="syntax-number">$1</span>');

        return text;
    }

    // ---------------------------------------------------------------------------
    // SSE Connection & Event Handling
    // ---------------------------------------------------------------------------

    let eventSource = null;
    let etaInterval = null;

    function connectSSE() {
        const goal = goalTextarea.value.trim();
        if (!goal) return;

        state.isRunning = true;
        state.currentRunId = null;
        state.activeAgents.clear();
        state.completedAgents.clear();
        state.phase = 0;
        state.streamingTokens = [];
        state.finalOutput = '';
        state.startTime = Date.now();

        // Reset UI
        clearGraph();
        outputContent.innerHTML = '<div class="output-empty">Initializing orchestration...</div>';
        outputTitle.classList.add('streaming');
        outputMeta.textContent = '';
        finalOutputSection.style.display = 'none';
        auditTimeline.innerHTML = '';
        auditCount.textContent = '0';
        state.auditEvents = [];

        // Show loading skeleton
        loadingSkeleton.style.display = 'flex';

        // Reset phase bar
        [phase1Step, phase2Step, phase3Step].forEach(el => el.classList.remove('active', 'completed'));
        [connector1, connector2].forEach(el => el.classList.remove('active'));

        // Update buttons
        setButtonsDisabled(true);
        runBtn.classList.add('running');
        runBtnText.textContent = 'Running...';
        updateStatus('Executing', true);

        // Show run info
        runInfo.style.display = 'flex';
        runIdDisplay.textContent = '-';
        phaseDisplay.textContent = 'Starting';
        activeNodesDisplay.textContent = '0';
        completedNodesDisplay.textContent = '0';
        progressDisplay.textContent = '0%';
        etaDisplay.textContent = '--';

        // Start ETA update interval
        etaInterval = setInterval(updateETA, 1000);

        // Show run content, hide empty state
        emptyState.style.display = 'none';
        runContent.style.display = 'flex';

        // Connect to SSE
        const url = `/execute`;
        eventSource = new EventSource(`${url}?goal=${encodeURIComponent(goal)}&mode=${encodeURIComponent(state.mode)}`);

        disconnectSSE();
        streamExecution(goal);
    }

    function disconnectSSE() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        if (etaInterval) {
            clearInterval(etaInterval);
            etaInterval = null;
        }
    }

    async function streamExecution(goal) {
        try {
            const response = await fetch('/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ goal, mode: state.mode }),
            });

            if (!response.ok) {
                addAuditEvent('error', `HTTP ${response.status}: ${response.statusText}`);
                updateStatus('Error');
                return;
            }

            // Hide skeleton once we start receiving data
            loadingSkeleton.style.display = 'none';

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        const eventType = line.slice(7).trim();
                        continue;
                    }
                    if (line.startsWith('data: ')) {
                        const jsonStr = line.slice(6);
                        try {
                            const data = JSON.parse(jsonStr);
                            handleEvent(data);
                        } catch (e) {
                            console.warn('JSON parse error:', e);
                        }
                    }
                }

                const rawLines = buffer.split('\n\n');
                if (rawLines.length > 1) {
                    buffer = rawLines.pop();
                    for (const segment of rawLines) {
                        parseSSESegment(segment);
                    }
                }
            }

            if (buffer) {
                parseSSESegment(buffer);
            }

        } catch (err) {
            addAuditEvent('error', err.message);
            updateStatus('Error');
            loadingSkeleton.style.display = 'none';
        } finally {
            finishRun();
        }
    }

    function parseSSESegment(segment) {
        const eventMatch = segment.match(/event: ([^\n]+)\n/);
        const dataMatch = segment.match(/data: (.+?)(?=\n\n|$)/s);

        if (dataMatch) {
            try {
                const data = JSON.parse(dataMatch[1]);
                const eventType = eventMatch ? eventMatch[1] : detectEventType(data);
                handleEvent(data, eventType);
            } catch (e) {
                try {
                    const data = JSON.parse(segment.trim());
                    handleEvent(data);
                } catch { /* ignore */ }
            }
        }
    }

    function detectEventType(data) {
        if (data.run_id && data.goal !== undefined) return 'run_started';
        if (data.phase !== undefined) return 'phase_start';
        if (data.node_type !== undefined) return 'node_started';
        if (data.output !== undefined) return 'final_output';
        if (data.run_id && data.confidence !== undefined) return 'run_completed';
        if (data.nodes !== undefined) return 'graph_built';
        return 'unknown';
    }

    function handleEvent(data, fallbackType) {
        const eventType = fallbackType || detectEventTypeFromData(data);

        switch (eventType) {
            case 'run_started':
                state.currentRunId = data.run_id;
                runIdDisplay.textContent = data.run_id.slice(0, 8) + '...';
                // Estimate ~30 seconds per phase
                state.estimatedTimeRemaining = 30000;
                addAuditEvent('phase_start', `Run started: ${data.goal.slice(0, 50)}...`);
                break;

            case 'phase_start':
                if (data.phase === 1) {
                    updatePhase(1);
                    addAuditEvent('phase_start', `Phase 1: ${data.description}`);
                    state.estimatedTimeRemaining = 30000;
                } else if (data.phase === 2) {
                    updatePhase(2);
                    addAuditEvent('phase_start', `Phase 2: ${data.description}`);
                    state.estimatedTimeRemaining = 45000;
                } else if (data.phase === 3) {
                    updatePhase(3);
                    addAuditEvent('phase_start', `Phase 3: ${data.description}`);
                    state.estimatedTimeRemaining = 30000;
                }
                break;

            case 'phase_complete':
                addAuditEvent('node_completed', `Seams identified: ${data.seams?.length || 0}`);
                if (data.seams) {
                    data.seams.forEach(seam => {
                        const nodeType = 'manager';
                        addNode(seam.manager_id, nodeType);
                        state.nodes[seam.manager_id].seamId = seam.id;
                    });
                }
                break;

            case 'graph_built':
                if (data.nodes) {
                    data.nodes.forEach(nodeId => {
                        if (!state.nodes[nodeId]) {
                            const nodeType = getNodeType(nodeId);
                            addNode(nodeId, nodeType);
                        }
                    });
                }
                addAuditEvent('node_completed', `Graph built: ${data.nodes?.length || 0} nodes`);
                positionNodes();
                break;

            case 'node_started':
                if (data.agent_id) {
                    state.activeAgents.add(data.agent_id);
                    const nodeType = data.node_type || getNodeType(data.agent_id);

                    if (!state.nodes[data.agent_id]) {
                        addNode(data.agent_id, nodeType);
                    }
                    setNodeStatus(data.agent_id, 'active', 'running');

                    activeNodesDisplay.textContent = state.activeAgents.size;
                    addAuditEvent('node_started', `${nodeType}: ${data.agent_id}`);
                    updateProgress();
                }
                break;

            case 'node_completed':
                if (data.agent_id) {
                    state.activeAgents.delete(data.agent_id);
                    state.completedAgents.add(data.agent_id);
                    setNodeStatus(data.agent_id, 'completed', 'done');
                    activeNodesDisplay.textContent = state.activeAgents.size;
                    completedNodesDisplay.textContent = state.completedAgents.size;
                    addAuditEvent('node_completed', `${data.agent_id}`);
                    updateEdgeLayer();
                    updateProgress();
                }
                break;

            case 'streaming_token':
                if (data.tokens) {
                    appendStreamingTokens(data.tokens);
                }
                break;

            case 'synthesis_progress':
                if (data.findings_keys) {
                    addAuditEvent('synthesis_progress', `Findings: ${data.findings_keys.join(', ')}`);
                }
                break;

            case 'final_output':
                if (data.output) {
                    displayFinalOutput(data.output);
                    const conf = (data.confidence * 100).toFixed(0);
                    addAuditEvent('run_completed', `Output: ${data.output.length} chars, confidence: ${conf}%`);
                    updatePhase(3);
                    state.estimatedTimeRemaining = 0;
                }
                break;

            case 'run_completed':
                addAuditEvent('run_completed', `Confidence: ${(data.confidence * 100).toFixed(0)}%`);
                state.estimatedTimeRemaining = 0;
                break;

            case 'run_failed':
            case 'error':
                addAuditEvent('error', data.message || 'Unknown error');
                updateStatus('Error');
                state.estimatedTimeRemaining = 0;
                break;

            default:
                if (data.type === 'streaming_token' && data.content) {
                    appendStreamingTokens([data.content]);
                }
                break;
        }
    }

    function detectEventTypeFromData(data) {
        if (data.phase !== undefined && data.name !== undefined) return 'phase_start';
        if (data.agent_id !== undefined && data.node_type !== undefined) return 'node_started';
        if (data.agent_id !== undefined && data.run_id !== undefined && data.output === undefined) return 'node_completed';
        if (data.output !== undefined) return 'final_output';
        if (data.run_id !== undefined && data.confidence !== undefined) return 'run_completed';
        if (data.nodes !== undefined) return 'graph_built';
        if (data.seams !== undefined) return 'phase_complete';
        if (data.run_id !== undefined && data.goal !== undefined) return 'run_started';
        if (data.error !== undefined) return 'error';
        return 'unknown';
    }

    function finishRun() {
        state.isRunning = false;

        if (etaInterval) {
            clearInterval(etaInterval);
            etaInterval = null;
        }

        setButtonsDisabled(false);
        runBtn.classList.remove('running');
        runBtnText.textContent = 'Run Orchestration';
        outputTitle.classList.remove('streaming');
        loadingSkeleton.style.display = 'none';

        if (state.currentRunId) {
            updateStatus('Completed');
        } else {
            updateStatus('Ready');
        }

        progressDisplay.textContent = '100%';
        etaDisplay.textContent = 'Done';
    }

    // ---------------------------------------------------------------------------
    // Actions
    // ---------------------------------------------------------------------------

    function copyOutput() {
        const text = state.streamingTokens.join('');
        if (text) {
            navigator.clipboard.writeText(text).then(() => {
                copyOutputBtn.innerHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    Copied!`;
                setTimeout(() => {
                    copyOutputBtn.innerHTML = `
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                        </svg>
                        Copy`;
                }, 1500);
            });
        }
    }

    function copyFinalOutput() {
        if (state.finalOutput) {
            navigator.clipboard.writeText(state.finalOutput).then(() => {
                copyFinalBtn.innerHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    Copied!`;
                setTimeout(() => {
                    copyFinalBtn.innerHTML = `
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                        </svg>
                        Copy`;
                }, 1500);
            });
        }
    }

    async function downloadAuditTrail() {
        if (!state.currentRunId) return;

        try {
            const response = await fetch(`/audit-trail/${state.currentRunId}`);
            if (!response.ok) throw new Error('Failed to fetch audit trail');

            const data = await response.json();
            const jsonStr = JSON.stringify(data.events, null, 2);
            const blob = new Blob([jsonStr], { type: 'application/json' });
            const url = URL.createObjectURL(blob);

            const a = document.createElement('a');
            a.href = url;
            a.download = `audit-trail-${state.currentRunId.slice(0, 8)}.json`;
            a.click();

            URL.revokeObjectURL(url);
        } catch (err) {
            addAuditEvent('error', `Download failed: ${err.message}`);
        }
    }

    async function downloadArtifacts() {
        if (!state.currentRunId) return;

        try {
            const response = await fetch(`/download/${state.currentRunId}`);
            if (!response.ok) throw new Error('Failed to download artifacts');

            const blob = await response.blob();
            const url = URL.createObjectURL(blob);

            const a = document.createElement('a');
            a.href = url;
            a.download = `artifacts-${state.currentRunId.slice(0, 8)}.zip`;
            a.click();

            URL.revokeObjectURL(url);
        } catch (err) {
            addAuditEvent('error', `Artifact download failed: ${err.message}`);
        }
    }

    // ---------------------------------------------------------------------------
    // File Tree
    // ---------------------------------------------------------------------------

    let filePollInterval = null;
    let knownFiles = new Set();

    function showFileTree() {
        document.getElementById('fileTreeHeader').style.display = 'flex';
        document.getElementById('fileTreePanel').style.display = 'block';
    }

    function updateFileTree(files) {
        const tree = document.getElementById('fileTree');
        const count = document.getElementById('fileCount');

        files.forEach(f => {
            if (!knownFiles.has(f.path)) {
                knownFiles.add(f.path);
                const li = document.createElement('li');
                li.style.padding = '3px 0';
                li.style.borderBottom = '1px solid var(--border-subtle)';
                li.innerHTML = `<span style="color: var(--accent-cyan);">&#x279C;</span> ${escapeHtml(f.path)}`;
                tree.appendChild(li);
            }
        });

        count.textContent = knownFiles.size;
    }

    function startFilePolling(runId) {
        stopFilePolling();
        knownFiles.clear();
        document.getElementById('fileTree').innerHTML = '';
        filePollInterval = setInterval(async () => {
            try {
                const resp = await fetch(`/files/${runId}`);
                if (resp.ok) {
                    const data = await resp.json();
                    if (data.files) {
                        updateFileTree(data.files);
                    }
                }
            } catch { /* ignore */ }
        }, 2000);
    }

    function stopFilePolling() {
        if (filePollInterval) {
            clearInterval(filePollInterval);
            filePollInterval = null;
        }
    }

    // ---------------------------------------------------------------------------
    // Event Listeners
    // ---------------------------------------------------------------------------

    document.querySelectorAll('.preset-chip').forEach(btn => {
        btn.addEventListener('click', () => {
            goalTextarea.value = btn.dataset.goal;
            goalTextarea.focus();
        });
    });

    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            state.mode = btn.dataset.mode;
        });
    });

    runBtn.addEventListener('click', () => {
        if (state.isRunning) {
            disconnectSSE();
            finishRun();
        } else {
            connectSSE();
        }
    });

    copyOutputBtn.addEventListener('click', copyOutput);
    copyFinalBtn.addEventListener('click', copyFinalOutput);
    downloadAuditBtn.addEventListener('click', downloadAuditTrail);
    downloadArtifactsBtn.addEventListener('click', downloadArtifacts);

    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(positionNodes, 100);
    });

    goalTextarea.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            if (!state.isRunning) {
                connectSSE();
            }
        }
    });

    async function loadRunHistory() {
        try {
            const response = await fetch('/runs');
            if (response.ok) {
                const data = await response.json();
                const historyEl = $('runHistory');
                if (data.runs && data.runs.length > 0) {
                    historyEl.innerHTML = data.runs.slice(0, 10).map(run => `
                        <div style="padding: 6px 0; font-size: 11px; color: var(--text-secondary); border-bottom: 1px solid var(--border-subtle);">
                            <span style="font-family: var(--font-mono); color: var(--text-muted);">${run.run_id.slice(0, 8)}...</span>
                            <span style="color: var(--border-bright); margin-left: 8px;">${run.event_count} events</span>
                        </div>
                    `).join('');
                }
            }
        } catch { /* ignore */ }
    }

    loadRunHistory();

})();