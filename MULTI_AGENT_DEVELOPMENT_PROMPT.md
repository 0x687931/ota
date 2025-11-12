# Multi-Agent Solution Discovery & Implementation - Best Practice Prompt

## Overview

This document provides a language-agnostic, best-practice prompt template for using multiple specialized agents to:
1. Explore diverse solution approaches
2. Implement competing alternatives in parallel
3. Evaluate solutions through comprehensive testing
4. Select the optimal implementation based on objective criteria

---

## The Prompt Template

### Version 1: Concise (Single Paragraph)

```
I need you to implement [SPECIFIC FEATURE/FIX] using specialized development agents.

For each identified issue, launch multiple agents in parallel to independently design and implement
competing solution approaches (minimum 3 alternatives per issue). Each agent should deliver:
(1) complete working implementation, (2) comprehensive test suite, (3) quantitative metrics
(performance, memory, complexity, maintainability).

All implementations must be non-breaking (backward compatible, no API changes, existing tests pass).

After all agents complete, perform comparative analysis across all solutions using objective criteria:
test coverage, performance benchmarks, code complexity metrics, edge case handling, and risk assessment.

Select the optimal solution for each issue based on weighted scoring of these criteria, then integrate
the winning implementations. Provide detailed justification for each selection with supporting data.
```

---

### Version 2: Detailed (Structured)

```
I need you to implement the following fixes/features using a multi-agent solution discovery approach.

## Objectives
1. Ensure diverse solution exploration (avoid groupthink)
2. Validate solutions through empirical testing
3. Select optimal implementations based on objective criteria
4. Maintain backward compatibility and code quality

## Process

### Phase 1: Solution Discovery (Parallel Agent Execution)

For each identified issue/requirement:

1. **Launch Multiple Specialized Agents** (3-5 agents per issue)
   - Each agent independently designs ONE unique solution approach
   - Agents should NOT see other agents' solutions during design phase
   - Assign different optimization priorities to each agent:
     * Agent A: Optimize for performance
     * Agent B: Optimize for memory efficiency
     * Agent C: Optimize for maintainability/simplicity
     * Agent D: Optimize for robustness/error handling
     * Agent E: Optimize for extensibility/future-proofing

2. **Agent Deliverables** (each agent must provide):
   - Complete working implementation
   - Comprehensive test suite (unit + integration tests)
   - Quantitative metrics:
     * Performance benchmarks (execution time, throughput)
     * Memory usage measurements (peak, average, allocation patterns)
     * Code complexity metrics (cyclomatic complexity, LOC, dependencies)
     * Test coverage percentage
   - Qualitative assessment:
     * Maintainability score (readability, documentation, patterns)
     * Risk level (LOW/MEDIUM/HIGH) with justification
     * Edge cases handled
     * Backward compatibility analysis

3. **Non-Breaking Constraints** (all implementations MUST satisfy):
   - ✅ Backward compatible (existing APIs unchanged)
   - ✅ No breaking behavior changes
   - ✅ All existing tests pass
   - ✅ No new runtime dependencies (unless explicitly approved)
   - ✅ Configuration changes are additive (optional new keys only)

### Phase 2: Comparative Evaluation

After all agents complete, perform systematic comparison:

1. **Create Comparison Matrix**
   - Rows: Solution approaches (A, B, C, D, E)
   - Columns: Evaluation criteria (performance, memory, complexity, etc.)
   - Cells: Quantitative measurements + qualitative ratings

2. **Evaluation Criteria** (weighted scoring):
   | Criterion | Weight | Measurement |
   |-----------|--------|-------------|
   | **Correctness** | 30% | Test pass rate, edge case coverage |
   | **Performance** | 20% | Benchmark results, scalability |
   | **Memory Efficiency** | 15% | Peak usage, allocations, leaks |
   | **Maintainability** | 15% | Code complexity, readability, documentation |
   | **Robustness** | 10% | Error handling, graceful degradation |
   | **Risk Level** | 10% | Implementation risk, testing coverage |

   *Adjust weights based on project priorities*

3. **Objective Selection Criteria**
   - Calculate weighted score for each solution
   - Identify clear winner OR hybrid approach (combine best aspects)
   - Document decision rationale with supporting data

### Phase 3: Integration & Validation

1. **Implement Winning Solutions**
   - Integrate selected implementations
   - Run full regression test suite
   - Perform integration testing across all fixes

2. **Cross-Fix Validation**
   - Ensure fixes don't conflict with each other
   - Verify combined memory/performance impact is acceptable
   - Test interaction effects between fixes

3. **Final Deliverables**
   - Implementation summary document
   - Comparative analysis report (why each solution was chosen)
   - Test results (before/after metrics)
   - Risk assessment and mitigation plan

## Agent Specialization Guidelines

When launching agents, specify their expertise and constraints:

### Example Agent Configurations

**Agent A: Performance Optimizer**
```
You are a [LANGUAGE] performance engineer. Optimize for speed and throughput.
Acceptable trade-offs: Slightly higher memory usage, moderate complexity increase.
Unacceptable: Sacrificing correctness, breaking backward compatibility.
Measure: Execution time, operations per second, latency percentiles.
```

**Agent B: Memory Efficiency Expert**
```
You are a [LANGUAGE] systems programmer specializing in memory-constrained environments.
Optimize for minimal memory footprint and zero allocations where possible.
Acceptable trade-offs: Slightly slower execution, streaming instead of batching.
Unacceptable: Memory leaks, unbounded growth, large intermediate buffers.
Measure: Peak memory, allocation count, heap fragmentation.
```

**Agent C: Maintainability Architect**
```
You are a [LANGUAGE] software architect focused on long-term maintainability.
Optimize for code clarity, simplicity, and standard patterns.
Acceptable trade-offs: Minor performance overhead for clearer code.
Unacceptable: Clever tricks, undocumented behavior, tight coupling.
Measure: Cyclomatic complexity, documentation coverage, pattern adherence.
```

**Agent D: Robustness Engineer**
```
You are a [LANGUAGE] reliability engineer. Optimize for error handling and edge cases.
Prioritize graceful degradation, clear error messages, and recovery mechanisms.
Acceptable trade-offs: Additional error-checking code, validation overhead.
Unacceptable: Silent failures, unclear error states, crash-prone code.
Measure: Error coverage, exception handling, input validation completeness.
```

**Agent E: Extensibility Designer**
```
You are a [LANGUAGE] API designer. Optimize for future extensibility and flexibility.
Design interfaces that can evolve without breaking changes.
Acceptable trade-offs: Additional abstraction layers, more upfront design.
Unacceptable: Hardcoded assumptions, tightly coupled implementations.
Measure: Interface stability, extension points, coupling metrics.
```

## Success Criteria

The multi-agent approach is successful when:

1. ✅ **Diversity**: Each agent produces genuinely different approaches (not minor variations)
2. ✅ **Completeness**: All agents deliver working, tested implementations
3. ✅ **Objectivity**: Selection based on measurable criteria, not subjective preference
4. ✅ **Justification**: Clear rationale for why each solution was chosen/rejected
5. ✅ **Quality**: Winning solutions pass all tests and meet quality standards
6. ✅ **Integration**: Selected solutions work together without conflicts

## Anti-Patterns to Avoid

❌ **Don't:**
- Launch agents sequentially (defeats parallel exploration)
- Share solutions between agents prematurely (creates bias)
- Select solutions based on gut feeling (use metrics)
- Ignore test results that contradict expectations
- Choose the first working solution (explore alternatives)
- Skip comparative analysis (defeats the purpose)

✅ **Do:**
- Launch all agents simultaneously (true parallel exploration)
- Keep agents independent until evaluation phase
- Weight criteria based on project priorities
- Trust empirical data over assumptions
- Document trade-offs explicitly
- Consider hybrid approaches combining best aspects

## Example Workflow

### Step 1: Issue Identification
```
Identified 4 critical issues requiring fixes:
- Issue A: [Description]
- Issue B: [Description]
- Issue C: [Description]
- Issue D: [Description]
```

### Step 2: Parallel Agent Launch
```
Launching 3 agents per issue (12 agents total):

Issue A:
  - Agent A1: Performance-optimized approach
  - Agent A2: Memory-optimized approach
  - Agent A3: Simplicity-optimized approach

Issue B:
  - Agent B1: Performance-optimized approach
  - Agent B2: Memory-optimized approach
  - Agent B3: Simplicity-optimized approach

[Repeat for Issues C and D]
```

### Step 3: Agent Execution
```
All agents work independently:
- Read relevant code
- Design solution
- Implement solution
- Write tests
- Measure metrics
- Report results
```

### Step 4: Comparative Analysis
```
Issue A Solutions Comparison:

| Metric | A1 (Perf) | A2 (Memory) | A3 (Simple) | Winner |
|--------|-----------|-------------|-------------|--------|
| Speed | 50ms | 80ms | 75ms | A1 ✅ |
| Memory | 2MB | 500KB | 1MB | A2 ✅ |
| Complexity | 8.5 | 6.2 | 4.1 | A3 ✅ |
| Test Coverage | 92% | 95% | 88% | A2 ✅ |
| Risk | MEDIUM | LOW | LOW | A2/A3 ✅ |

Weighted Score:
- A1: 72/100
- A2: 85/100 ⭐ WINNER
- A3: 79/100

Decision: Select A2 (memory-optimized) because:
1. Best weighted score (85/100)
2. Lowest risk (LOW vs. MEDIUM)
3. Memory savings critical for target platform
4. Performance difference acceptable (30ms overhead)
```

### Step 5: Integration
```
Selected solutions:
- Issue A: Agent A2 approach (memory-optimized)
- Issue B: Agent B1 approach (performance-optimized)
- Issue C: Agent C3 approach (simplicity-optimized)
- Issue D: Hybrid of D2 + D3 (combined best aspects)

Integration testing:
✅ All 90 tests pass
✅ Combined memory impact: +2.5MB (acceptable)
✅ Combined performance impact: +45ms (acceptable)
✅ No conflicts between solutions
```

## Language-Agnostic Adaptations

### For Python
- Measure: `memory_profiler`, `cProfile`, `pytest-benchmark`
- Metrics: Cyclomatic complexity via `radon`, test coverage via `pytest-cov`

### For JavaScript/TypeScript
- Measure: `clinic.js`, `0x`, `jest --coverage`
- Metrics: Complexity via `complexity-report`, bundle size via `webpack-bundle-analyzer`

### For Rust
- Measure: `criterion`, `cargo-flamegraph`, `valgrind`
- Metrics: `cargo-clippy`, `cargo-tarpaulin` for coverage

### For Go
- Measure: `go test -bench`, `pprof`, `go test -cover`
- Metrics: `gocyclo`, `golangci-lint`

### For Java
- Measure: JMH benchmarks, JProfiler, JaCoCo coverage
- Metrics: SonarQube, Checkstyle, PMD

### For C/C++
- Measure: Google Benchmark, Valgrind, gcov/lcov
- Metrics: Complexity via `pmccabe`, static analysis via `cppcheck`

## Summary

This multi-agent approach ensures:
1. **Diverse exploration** of solution space
2. **Objective selection** based on empirical data
3. **High-quality outcomes** through competitive pressure
4. **Documented trade-offs** for future maintainers
5. **Reduced bias** from independent parallel work

The key is to let agents work independently, measure objectively, and select based on
data rather than intuition.
```

---

## Version 3: Minimal (For Quick Requests)

```
Implement [FEATURE/FIX] using multiple specialized agents:

1. Launch 3+ agents in parallel, each designing a unique solution approach
2. Each agent delivers: working code + tests + metrics (performance, memory, complexity)
3. All implementations must be non-breaking (backward compatible, existing tests pass)
4. Create comparison matrix with objective criteria
5. Select optimal solution(s) based on weighted scoring
6. Provide detailed justification with supporting data

Optimize different agents for: performance, memory, maintainability, robustness.
```

---

## Real-World Example (Language-Agnostic)

### Original Prompt (Specific)
```
I would now like you to develop using specialized Python coders and critically defined
agents to implement fixes along with critical tests; implement only in a non-breaking way.
Use multiple agents to determine multiple solutions picking the best one based on testing.
```

### Improved Prompt (Language-Agnostic)
```
Implement the four identified critical fixes using a multi-agent solution discovery approach:

**Phase 1 - Parallel Solution Exploration:**
Launch 3 specialized agents per fix (12 agents total):
- Agent Type A: Optimize for minimal code changes (low risk)
- Agent Type B: Optimize for performance/efficiency
- Agent Type C: Optimize for maintainability and extensibility

Each agent must deliver:
1. Complete implementation in worktree (non-breaking, backward compatible)
2. Comprehensive test suite (unit + integration + edge cases)
3. Quantitative metrics:
   - Lines of code changed
   - Test coverage percentage
   - Performance benchmarks (execution time, memory usage)
   - Complexity metrics (cyclomatic complexity, dependencies)
4. Risk assessment (LOW/MEDIUM/HIGH) with justification

**Phase 2 - Comparative Evaluation:**
Create comparison matrices for each fix showing:
- Implementation approach (algorithm, data structures, patterns)
- Metrics comparison (LOC, performance, memory, complexity)
- Trade-off analysis (pros/cons of each approach)
- Risk level with mitigation strategies

**Phase 3 - Selection & Integration:**
For each fix, select the optimal solution based on weighted criteria:
- Correctness (30%): Test pass rate, edge cases covered
- Risk Level (25%): Implementation complexity, test coverage
- Performance (20%): Execution time, memory efficiency
- Maintainability (15%): Code clarity, documentation
- Backward Compatibility (10%): Zero breaking changes

Integrate winning solutions and validate:
✅ All existing tests pass (no regressions)
✅ All new tests pass (comprehensive coverage)
✅ Combined solutions don't conflict
✅ Total impact is acceptable (performance, memory, LOC)

**Final Deliverable:**
Implementation summary with:
- Comparison matrices for all 4 fixes
- Selection rationale for each winning solution (data-driven)
- Combined test results (before/after metrics)
- Risk assessment and integration validation
```

---

## Key Improvements in Language-Agnostic Version

### 1. **Removed Language-Specific References**
   - ❌ "Python coders"
   - ✅ "specialized development agents"

### 2. **Added Clear Process Structure**
   - Phase 1: Parallel Exploration
   - Phase 2: Comparative Evaluation
   - Phase 3: Selection & Integration

### 3. **Defined Objective Criteria**
   - Quantitative metrics (measurable)
   - Weighted scoring (prioritized)
   - Data-driven selection (justified)

### 4. **Specified Agent Diversity**
   - Different optimization goals per agent
   - Prevents similar solutions
   - Ensures exploration of solution space

### 5. **Explicit Constraints**
   - Non-breaking changes required
   - Backward compatibility mandatory
   - Existing tests must pass

### 6. **Comprehensive Deliverables**
   - Working implementation
   - Test suite
   - Metrics
   - Risk assessment
   - Comparison matrices
   - Selection justification

### 7. **Validation Requirements**
   - Individual solution validation
   - Combined integration testing
   - Regression testing
   - Impact assessment

---

## When to Use This Approach

### ✅ **Good Use Cases:**
- Critical fixes where correctness is paramount
- Performance-sensitive optimizations
- Memory-constrained environments
- High-risk changes requiring validation
- Architectural decisions with multiple valid approaches
- Refactoring with unclear optimal strategy

### ❌ **Avoid For:**
- Trivial changes (overkill)
- Single obvious solution (unnecessary parallelism)
- Time-critical hotfixes (too slow)
- Well-established patterns (reinventing wheel)
- Simple bug fixes (disproportionate effort)

---

## Metrics & Measurements

### Performance Benchmarks
```
Measure:
- Execution time (average, p50, p95, p99)
- Throughput (operations per second)
- Latency (response time)
- CPU utilization
- I/O operations count
```

### Memory Metrics
```
Measure:
- Peak memory usage (max RSS)
- Average memory usage
- Allocation count
- Deallocation count
- Memory leaks (growth over time)
- Heap fragmentation
```

### Code Complexity
```
Measure:
- Cyclomatic complexity (per function, average)
- Lines of code (LOC)
- Number of dependencies
- Depth of inheritance/nesting
- Coupling metrics (afferent/efferent)
```

### Quality Metrics
```
Measure:
- Test coverage (line, branch, path)
- Test count (unit, integration, e2e)
- Documentation coverage
- Static analysis warnings
- Code review feedback score
```

---

## Template Checklist

When using this approach, ensure:

- [ ] Multiple agents launched (minimum 3 per issue)
- [ ] Agents have different optimization priorities
- [ ] All agents deliver complete implementations
- [ ] Comprehensive test suites included
- [ ] Quantitative metrics measured
- [ ] Comparison matrices created
- [ ] Selection criteria defined and weighted
- [ ] Winning solutions justified with data
- [ ] Integration validation performed
- [ ] Documentation complete

---

## Conclusion

This language-agnostic, multi-agent approach ensures:

1. **Exploration** of diverse solution space
2. **Validation** through comprehensive testing
3. **Objectivity** in solution selection
4. **Quality** through competitive pressure
5. **Documentation** of trade-offs and rationale

The result is robust, well-tested implementations chosen based on empirical evidence rather than assumptions or bias.
