/**
 * SQL Query Builder - Frontend Component
 * 
 * Visual query builder for table and chart elements in the report designer.
 */

class QueryBuilder {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.options = {
            connectionId: options.connectionId || null,
            initialConfig: options.initialConfig || null,
            onSave: options.onSave || null,
            onCancel: options.onCancel || null,
            ...options
        };
        
        this.queryConfig = this.options.initialConfig || this.createEmptyConfig();
        this.schema = null;
        this.isOpen = false;
        
        this.init();
    }
    
    createEmptyConfig() {
        return {
            version: "1.0",
            select: [],
            from_tables: [],
            joins: [],
            where: [],
            group_by: [],
            order_by: []
        };
    }
    
    async init() {
        this.render();
        this.bindEvents();
        
        if (this.options.connectionId) {
            await this.loadSchema();
        }
    }
    
    render() {
        const container = document.getElementById(this.containerId);
        if (!container) return;
        
        container.innerHTML = `
            <div class="query-builder-modal" style="display: none;">
                <div class="query-builder-backdrop"></div>
                <div class="query-builder-container">
                    <div class="query-builder-header">
                        <h3>🔨 Visual Query Builder</h3>
                        <div class="query-builder-actions">
                            <button class="btn btn-secondary" onclick="queryBuilder.close()">Cancel</button>
                            <button class="btn btn-primary" onclick="queryBuilder.reset()">Reset</button>
                            <button class="btn btn-success" onclick="queryBuilder.save()">Save Query</button>
                        </div>
                    </div>
                    
                    <div class="query-builder-body">
                        <!-- Schema Browser -->
                        <div class="schema-panel">
                            <h4>📊 Tables</h4>
                            <div class="schema-tables" id="schemaTables">
                                <p class="text-muted">Loading schema...</p>
                            </div>
                        </div>
                        
                        <!-- Query Canvas -->
                        <div class="query-canvas">
                            <div class="canvas-toolbar">
                                <button class="btn btn-sm" onclick="queryBuilder.addStep('select')">➕ SELECT</button>
                                <button class="btn btn-sm" onclick="queryBuilder.addStep('from')">➕ FROM</button>
                                <button class="btn btn-sm" onclick="queryBuilder.addStep('join')">➕ JOIN</button>
                                <button class="btn btn-sm" onclick="queryBuilder.addStep('where')">➕ WHERE</button>
                                <button class="btn btn-sm" onclick="queryBuilder.addStep('groupby')">➕ GROUP BY</button>
                                <button class="btn btn-sm" onclick="queryBuilder.addStep('orderby')">➕ ORDER BY</button>
                            </div>
                            
                            <div class="query-steps" id="querySteps">
                                <!-- Steps will be dynamically added here -->
                            </div>
                        </div>
                        
                        <!-- SQL Preview -->
                        <div class="sql-panel">
                            <h4>📝 Generated SQL</h4>
                            <pre class="sql-output" id="sqlOutput">-- Select tables and columns to build your query</pre>
                            
                            <div class="sql-actions">
                                <button class="btn btn-primary" onclick="queryBuilder.testQuery()">▶ Test</button>
                                <button class="btn btn-secondary" onclick="queryBuilder.copySQL()">📋 Copy</button>
                            </div>
                            
                            <div id="testResult"></div>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        this.modal = container.querySelector('.query-builder-modal');
        this.isOpen = true;
        this.modal.style.display = 'block';
    }
    
    bindEvents() {
        // Close on backdrop click
        this.modal.querySelector('.query-builder-backdrop').addEventListener('click', () => {
            this.close();
        });
        
        // Close on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isOpen) {
                this.close();
            }
        });
    }
    
    async loadSchema() {
        try {
            const response = await fetch(`/api/query-builder/schema/${this.options.connectionId}`);
            if (!response.ok) throw new Error('Failed to load schema');
            
            this.schema = await response.json();
            this.renderSchema();
        } catch (error) {
            console.error('Error loading schema:', error);
            document.getElementById('schemaTables').innerHTML = 
                '<p class="text-danger">Failed to load schema</p>';
        }
    }
    
    renderSchema() {
        const container = document.getElementById('schemaTables');
        if (!this.schema || !this.schema.tables) {
            container.innerHTML = '<p class="text-muted">No tables available</p>';
            return;
        }
        
        container.innerHTML = this.schema.tables.map(table => `
            <div class="schema-table" data-table="${table.name}">
                <div class="table-header" onclick="this.nextElementSibling.style.display = this.nextElementSibling.style.display === 'none' ? 'block' : 'none'">
                    <span class="table-icon">📋</span>
                    <span class="table-name">${table.name}</span>
                </div>
                <div class="table-columns" style="display: none;">
                    ${table.columns.map(col => `
                        <div class="column-item" draggable="true" 
                             data-table="${table.name}" 
                             data-column="${col.name}"
                             data-type="${col.data_type}"
                             onclick="queryBuilder.addColumn('${table.name}', '${col.name}')">
                            ${col.name}
                            <span class="column-type">${col.data_type}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `).join('');
    }
    
    addColumn(tableName, columnName) {
        // Add column to SELECT step
        const selectStep = document.querySelector('[data-step-type="select"]');
        if (!selectStep) {
            this.addStep('select');
        }
        
        const columnsContainer = selectStep.querySelector('.selected-columns');
        const columnTag = document.createElement('div');
        columnTag.className = 'column-tag';
        columnTag.innerHTML = `
            ${tableName}.${columnName}
            <select class="agg-select" onchange="queryBuilder.updateAggregation(this)">
                <option value="">None</option>
                <option value="COUNT">COUNT</option>
                <option value="SUM">SUM</option>
                <option value="AVG">AVG</option>
                <option value="MIN">MIN</option>
                <option value="MAX">MAX</option>
            </select>
            <span class="remove" onclick="this.parentElement.remove(); queryBuilder.regenerateSQL()">✕</span>
        `;
        columnsContainer.appendChild(columnTag);
        
        this.regenerateSQL();
    }
    
    updateAggregation(select) {
        const tag = select.closest('.column-tag');
        const agg = select.value;
        tag.dataset.aggregation = agg || null;
        this.regenerateSQL();
    }
    
    addStep(type) {
        const stepsContainer = document.getElementById('querySteps');
        const stepId = `step-${Date.now()}`;
        
        let stepHtml = '';
        
        switch(type) {
            case 'select':
                stepHtml = `
                    <div class="step-card" data-step-type="select" id="${stepId}">
                        <div class="step-header">
                            <div class="step-title">
                                <div class="step-number">1</div>
                                SELECT Columns
                            </div>
                            <button class="btn btn-sm btn-secondary" onclick="queryBuilder.removeStep('${stepId}')">✕</button>
                        </div>
                        <div class="step-content">
                            <div class="selected-columns" id="columns-${stepId}"></div>
                        </div>
                    </div>
                `;
                break;
                
            case 'from':
                stepHtml = `
                    <div class="step-card" data-step-type="from" id="${stepId}">
                        <div class="step-header">
                            <div class="step-title">
                                <div class="step-number">2</div>
                                FROM Tables
                            </div>
                            <button class="btn btn-sm btn-secondary" onclick="queryBuilder.removeStep('${stepId}')">✕</button>
                        </div>
                        <div class="step-content">
                            <div class="selected-tables" id="tables-${stepId}"></div>
                        </div>
                    </div>
                `;
                break;
                
            case 'join':
                stepHtml = `
                    <div class="step-card" data-step-type="join" id="${stepId}">
                        <div class="step-header">
                            <div class="step-title">
                                <div class="step-number">3</div>
                                JOIN
                            </div>
                            <button class="btn btn-sm btn-secondary" onclick="queryBuilder.removeStep('${stepId}')">✕</button>
                        </div>
                        <div class="step-content">
                            <div class="join-config">
                                <select class="join-table-select" onchange="queryBuilder.updateJoin(this)">
                                    <option value="">Select table...</option>
                                    ${this.schema?.tables.map(t => `<option value="${t.name}">${t.name}</option>`).join('')}
                                </select>
                                <select class="join-type-select">
                                    <option value="INNER">INNER JOIN</option>
                                    <option value="LEFT">LEFT JOIN</option>
                                    <option value="RIGHT">RIGHT JOIN</option>
                                </select>
                                <input type="text" class="join-condition-input" placeholder="ON condition (e.g., t1.id = t2.fk)" onchange="queryBuilder.updateJoin(this)">
                            </div>
                        </div>
                    </div>
                `;
                break;
                
            case 'where':
                stepHtml = `
                    <div class="step-card" data-step-type="where" id="${stepId}">
                        <div class="step-header">
                            <div class="step-title">
                                <div class="step-number">4</div>
                                WHERE Filters
                            </div>
                            <button class="btn btn-sm btn-secondary" onclick="queryBuilder.removeStep('${stepId}')">✕</button>
                        </div>
                        <div class="step-content">
                            <div class="filter-config">
                                <div class="filter-row">
                                    <select class="filter-field-select">
                                        <option value="">Select field...</option>
                                    </select>
                                    <select class="filter-operator-select">
                                        <option value="=">=</option>
                                        <option value=">">></option>
                                        <option value="<"><</option>
                                        <option value="LIKE">LIKE</option>
                                    </select>
                                    <input type="text" class="filter-value-input" placeholder="Value">
                                </div>
                            </div>
                        </div>
                    </div>
                `;
                break;
                
            case 'groupby':
                stepHtml = `
                    <div class="step-card" data-step-type="groupby" id="${stepId}">
                        <div class="step-header">
                            <div class="step-title">
                                <div class="step-number">5</div>
                                GROUP BY
                            </div>
                            <button class="btn btn-sm btn-secondary" onclick="queryBuilder.removeStep('${stepId}')">✕</button>
                        </div>
                        <div class="step-content">
                            <div class="selected-columns" id="groupby-${stepId}"></div>
                        </div>
                    </div>
                `;
                break;
                
            case 'orderby':
                stepHtml = `
                    <div class="step-card" data-step-type="orderby" id="${stepId}">
                        <div class="step-header">
                            <div class="step-title">
                                <div class="step-number">6</div>
                                ORDER BY
                            </div>
                            <button class="btn btn-sm btn-secondary" onclick="queryBuilder.removeStep('${stepId}')">✕</button>
                        </div>
                        <div class="step-content">
                            <div class="orderby-config">
                                <select class="orderby-field-select">
                                    <option value="">Select field...</option>
                                </select>
                                <select class="orderby-direction-select">
                                    <option value="ASC">ASC</option>
                                    <option value="DESC">DESC</option>
                                </select>
                            </div>
                        </div>
                    </div>
                `;
                break;
        }
        
        stepsContainer.insertAdjacentHTML('beforeend', stepHtml);
        this.regenerateSQL();
    }
    
    removeStep(stepId) {
        const step = document.getElementById(stepId);
        if (step) {
            step.remove();
            this.regenerateSQL();
        }
    }
    
    regenerateSQL() {
        // Collect all steps and generate SQL
        const steps = document.querySelectorAll('.step-card');
        let sql = '';
        
        // SELECT
        const selectSteps = steps.filter(s => s.dataset.stepType === 'select');
        if (selectSteps.length > 0) {
            const columns = [];
            selectSteps.forEach(step => {
                step.querySelectorAll('.column-tag').forEach(tag => {
                    const text = tag.textContent.trim();
                    const agg = tag.dataset.aggregation;
                    if (agg) {
                        columns.push(`${agg}(${text})`);
                    } else {
                        columns.push(text);
                    }
                });
            });
            sql += `SELECT ${columns.join(', ')}\n`;
        }
        
        // FROM
        const fromSteps = steps.filter(s => s.dataset.stepType === 'from');
        if (fromSteps.length > 0) {
            const tables = [];
            fromSteps.forEach(step => {
                step.querySelectorAll('.table-tag').forEach(tag => {
                    tables.push(tag.textContent.trim());
                });
            });
            if (tables.length > 0) {
                sql += `FROM ${tables[0]}\n`;
            }
        }
        
        // JOINs
        const joinSteps = steps.filter(s => s.dataset.stepType === 'join');
        joinSteps.forEach(step => {
            const table = step.querySelector('.join-table-select')?.value;
            const type = step.querySelector('.join-type-select')?.value;
            const condition = step.querySelector('.join-condition-input')?.value;
            
            if (table && condition) {
                sql += `${type} JOIN ${table} ON ${condition}\n`;
            }
        });
        
        // WHERE
        const whereSteps = steps.filter(s => s.dataset.stepType === 'where');
        const filters = [];
        whereSteps.forEach(step => {
            const field = step.querySelector('.filter-field-select')?.value;
            const operator = step.querySelector('.filter-operator-select')?.value;
            const value = step.querySelector('.filter-value-input')?.value;
            
            if (field && value) {
                filters.push(`${field} ${operator} '${value}'`);
            }
        });
        if (filters.length > 0) {
            sql += `WHERE ${filters.join(' AND ')}\n`;
        }
        
        // GROUP BY
        const groupBySteps = steps.filter(s => s.dataset.stepType === 'groupby');
        if (groupBySteps.length > 0) {
            const columns = [];
            groupBySteps.forEach(step => {
                step.querySelectorAll('.column-tag').forEach(tag => {
                    columns.push(tag.textContent.trim());
                });
            });
            if (columns.length > 0) {
                sql += `GROUP BY ${columns.join(', ')}\n`;
            }
        }
        
        // ORDER BY
        const orderBySteps = steps.filter(s => s.dataset.stepType === 'orderby');
        if (orderBySteps.length > 0) {
            const fields = [];
            orderBySteps.forEach(step => {
                const field = step.querySelector('.orderby-field-select')?.value;
                const direction = step.querySelector('.orderby-direction-select')?.value;
                if (field) {
                    fields.push(`${field} ${direction}`);
                }
            });
            if (fields.length > 0) {
                sql += `ORDER BY ${fields.join(', ')}`;
            }
        }
        
        document.getElementById('sqlOutput').textContent = sql || '-- Select tables and columns to build your query';
    }
    
    async testQuery() {
        const resultDiv = document.getElementById('testResult');
        resultDiv.innerHTML = '<div class="test-result">⏳ Testing query...</div>';
        
        try {
            const response = await fetch('/api/query-builder/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query_config: this.queryConfig,
                    connection_id: this.options.connectionId,
                    limit: 100
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                resultDiv.innerHTML = `<div class="test-result success">✅ Query executed successfully! Returned ${result.row_count} rows.</div>`;
            } else {
                resultDiv.innerHTML = `<div class="test-result error">❌ ${result.message || 'Query failed'}</div>`;
            }
        } catch (error) {
            resultDiv.innerHTML = `<div class="test-result error">❌ Error: ${error.message}</div>`;
        }
    }
    
    copySQL() {
        const sql = document.getElementById('sqlOutput').textContent;
        navigator.clipboard.writeText(sql).then(() => {
            alert('SQL copied to clipboard!');
        });
    }
    
    save() {
        if (this.options.onSave) {
            this.options.onSave(this.queryConfig);
        }
        this.close();
    }
    
    reset() {
        if (confirm('Reset all query steps?')) {
            document.getElementById('querySteps').innerHTML = '';
            document.getElementById('sqlOutput').textContent = '-- Select tables and columns to build your query';
            this.queryConfig = this.createEmptyConfig();
        }
    }
    
    close() {
        this.isOpen = false;
        this.modal.style.display = 'none';
        
        if (this.options.onCancel) {
            this.options.onCancel();
        }
    }
}

// Global instance
let queryBuilder = null;

// Initialize query builder
function initQueryBuilder(containerId, options) {
    queryBuilder = new QueryBuilder(containerId, options);
    return queryBuilder;
}
