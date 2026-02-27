import os
import re
import json
import base64
import sqlite3
import html
from io import BytesIO
from flask import Flask, request, jsonify, render_template_string
from google import genai
from google.genai import types

# ReportLab Imports for PDF Generation
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, Flowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'lab_records.db')
LOGO_PATH = os.path.join(BASE_DIR, 'download.png')

# --- Helper: Dynamic Gemini Client ---
def get_gemini_client():
    api_key = request.headers.get('X-API-Key', '').strip()
    if not api_key:
        raise ValueError("Gemini API Key is missing! Please click 'Settings' at the top right to add it.")
    return genai.Client(api_key=api_key)


# --- Custom Flowable to Force Content to the Bottom ---
class PushToBottomSpacer(Flowable):
    """Dynamically calculates remaining page height and consumes it, forcing the next elements to the bottom edge."""
    def __init__(self, elements_to_push):
        Flowable.__init__(self)
        self.elements_to_push = elements_to_push

    def wrap(self, availWidth, availHeight):
        # Calculate exactly how much height the target elements need
        h = 0
        for el in self.elements_to_push:
            w, eh = el.wrap(availWidth, availHeight)
            h += eh
        
        # Add a tiny buffer so it doesn't accidentally trigger a page break by 1 pixel
        total_needed = h + (0.05 * inch)

        # If there isn't enough room for the result block, ask for more than available to trigger a page break
        if availHeight < total_needed:
            self.height = availHeight + 10 
        else:
            # Consume all empty space until exactly 'total_needed' is left
            self.height = availHeight - total_needed
        return (availWidth, self.height)

    def draw(self):
        pass # Invisible spacer

# --- Frontend HTML/JS Embedded ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>C Lab Record Generator</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <!-- Import Authentic Retro DOS Font for Turbo C styling -->
    <link href="https://fonts.googleapis.com/css2?family=VT323&display=swap" rel="stylesheet">
    
    <style>
        body { background-color: #f0f2f5; color: #212529; font-family: Arial, "Helvetica Neue", Helvetica, sans-serif; margin: 0; padding: 0; }
        .header-bar { background-color: #ffffff; border-bottom: 2px solid #0056b3; box-shadow: 0 2px 4px rgba(0,0,0,0.05); padding: 15px 20px; }
        .card { background: #ffffff; border: 1px solid #ced4da; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); padding: 25px; margin-top: 20px; }
        .form-label { display: block; font-weight: bold; color: #495057; margin-bottom: 5px; font-size: 0.9rem; }
        .form-control { width: 100%; padding: 8px 12px; font-size: 0.95rem; line-height: 1.5; color: #495057; background-color: #fff; border: 1px solid #ced4da; border-radius: 4px; transition: border-color 0.2s; box-sizing: border-box; }
        .form-control:focus { border-color: #80bdff; outline: none; box-shadow: 0 0 0 0.2rem rgba(0, 123, 255, 0.25); }
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; font-weight: bold; border: 1px solid transparent; padding: 8px 16px; font-size: 0.95rem; border-radius: 4px; cursor: pointer; transition: background-color 0.2s; }
        .btn-sm { padding: 5px 10px; font-size: 0.85rem; }
        .btn-primary { color: #fff; background-color: #0056b3; border-color: #004a99; }
        .btn-primary:hover { background-color: #004494; }
        .btn-success { color: #fff; background-color: #28a745; border-color: #218838; }
        .btn-success:hover { background-color: #218838; }
        .btn-secondary { color: #495057; background-color: #e9ecef; border-color: #ced4da; }
        .btn-secondary:hover { background-color: #dde2e6; }
        .btn-warning { color: #856404; background-color: #ffeeba; border-color: #ffeeba; }
        .btn-warning:hover { background-color: #f5d376; }
        .btn-text { background: none; border: none; color: #0056b3; cursor: pointer; font-weight: bold; }
        .btn-text:hover { text-decoration: underline; color: #004494; }
        .btn-text-danger { background: none; border: none; color: #dc3545; cursor: pointer; font-weight: bold; }
        .btn-text-danger:hover { text-decoration: underline; color: #c82333; }
        
        /* Authentic Turbo C++ Styling using VT323 Google Font */
        .turbo-c-terminal { 
            background-color: #000000; 
            color: #c0c0c0; /* Authentic DOS Light Gray */
            font-family: 'VT323', Courier, monospace; 
            border: 3px inset #6c757d; 
            border-radius: 0px; 
            padding: 10px; 
            font-size: 1.3rem; /* VT323 needs to be slightly larger */
            line-height: 1.1;
            letter-spacing: 1px;
        }
        
        .hidden { display: none !important; }
        table.dashboard-table { width: 100%; border-collapse: collapse; }
        table.dashboard-table th, table.dashboard-table td { padding: 12px; border-bottom: 1px solid #dee2e6; text-align: left; }
        table.dashboard-table th { background-color: #f8f9fa; color: #495057; }
        .spinner { display: inline-block; width: 1rem; height: 1rem; border: 3px solid rgba(255,255,255,0.3); border-radius: 50%; border-top-color: #fff; animation: spin 1s ease-in-out infinite; }
        .spinner-dark { border: 3px solid rgba(0,0,0,0.1); border-top-color: #000; }
        @keyframes spin { to { transform: rotate(360deg); } }
        
        /* Highlight Display Div */
        #r_program_display {
            min-height: 17rem;
            white-space: pre;
            overflow-x: auto;
        }
        .error-line-highlight {
            background-color: #fca5a5; /* Tailwind red-300 */
            color: #7f1d1d; /* Tailwind red-900 */
            display: inline-block;
            width: 100%;
            font-weight: bold;
        }
        
        /* Modal Overlay */
        #settingsModal { background-color: rgba(0, 0, 0, 0.6); backdrop-filter: blur(2px); }
    </style>
</head>
<body>

    <!-- Header -->
    <header class="header-bar">
        <div class="max-w-6xl mx-auto flex justify-between items-center">
            <div class="text-xl font-bold text-gray-800 flex items-center gap-2">
                <i class="fa-solid fa-file-signature text-blue-700"></i> C Lab Record Generator
            </div>
            <div class="flex items-center gap-3">
                <div class="text-sm font-bold text-gray-500 border border-gray-300 px-3 py-1 rounded bg-gray-50">
                    <i class="fa-solid fa-database"></i> Local DB Active
                </div>
                <button id="openSettingsBtn" class="btn btn-sm btn-secondary"><i class="fa-solid fa-gear"></i> Settings</button>
            </div>
        </div>
    </header>

    <!-- Settings Modal -->
    <div id="settingsModal" class="hidden fixed inset-0 flex justify-center items-center z-50">
        <div class="bg-white p-6 rounded shadow-lg w-96 max-w-full">
            <h3 class="text-xl font-bold mb-4 text-gray-800"><i class="fa-solid fa-key"></i> App Settings</h3>
            <label class="form-label text-sm text-gray-600 mb-1">Gemini AI API Key</label>
            <p class="text-xs text-gray-500 mb-3">Paste your Google Gemini API Key below. It will be stored securely in your browser.</p>
            <!-- Type=password prevents copying or viewing -->
            <input type="password" id="apiKeyInput" class="form-control mb-5" placeholder="Enter API Key">
            <div class="flex justify-end gap-2">
                <button id="closeSettingsBtn" class="btn btn-secondary">Cancel</button>
                <button id="saveSettingsBtn" class="btn btn-primary">Save Key</button>
            </div>
        </div>
    </div>

    <main class="max-w-6xl mx-auto px-4 pb-10">
        <section id="dashboardSection">
            <div class="flex justify-between items-center mt-8 border-b border-gray-300 pb-2">
                <h2 class="text-2xl font-bold text-gray-800">Experiment Directory</h2>
                <button id="createNewBtn" class="btn btn-primary"><i class="fa-solid fa-plus"></i> Create New Record</button>
            </div>
            <div class="card">
                <table class="dashboard-table">
                    <thead><tr><th>Exp. No</th><th>Title</th><th>Date Created</th><th class="text-right">Actions</th></tr></thead>
                    <tbody id="experimentsList"></tbody>
                </table>
                <div id="noRecordsMsg" class="text-center text-gray-500 py-12 hidden">
                    <p class="mb-2"><i class="fa-regular fa-folder-open text-4xl text-gray-400"></i></p>
                    <p>No records found. Click 'Create New Record' to begin.</p>
                </div>
            </div>
        </section>

        <section id="editorSection" class="hidden">
            <!-- Row 1: Back Button & Title -->
            <div class="flex items-center gap-4 mt-8 border-b border-gray-300 pb-3 mb-4">
                <button id="backToDashBtn" class="btn btn-secondary">
                    <i class="fa-solid fa-arrow-left"></i> Back
                </button>
                <h2 class="text-2xl font-bold text-gray-800">Record Editor</h2>
            </div>
            
            <!-- Row 2: Select Files -->
            <div class="mb-3 bg-blue-50 border border-blue-200 p-3 rounded">
                <label class="form-label text-blue-900"><i class="fa-solid fa-file-image"></i> Select Files (Max 5)</label>
                <input type="file" id="imageUpload" accept="image/*" multiple class="form-control bg-white">
            </div>

            <!-- Row 3: Scan Pages -->
            <div class="mb-6">
                <button id="scanBtn" class="btn btn-primary w-full py-3 shadow-sm">
                    <i class="fa-solid fa-robot"></i> Scan Pages <div id="scanSpinner" class="spinner hidden ml-2"></div>
                </button>
            </div>

            <div class="card">
                <form id="recordForm" class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <input type="hidden" id="r_id">
                    
                    <div class="md:col-span-2 border-b border-gray-200 pb-2">
                        <h4 class="text-lg font-bold text-gray-700">Header Information</h4>
                    </div>

                    <div>
                        <label class="form-label">Student Name</label>
                        <input type="text" id="r_name" class="form-control" required>
                    </div>
                    <div>
                        <label class="form-label">Register Number</label>
                        <input type="text" id="r_regno" class="form-control" maxlength="13" required>
                    </div>
                    <div>
                        <label class="form-label">Experiment No.</label>
                        <input type="text" id="r_expno" class="form-control" required>
                    </div>
                    <div>
                        <label class="form-label">Date (Optional)</label>
                        <input type="text" id="r_date" class="form-control" placeholder="DD/MM/YYYY or blank">
                    </div>

                    <div class="md:col-span-2 border-b border-gray-200 pb-2 mt-4">
                        <h4 class="text-lg font-bold text-gray-700">Experiment Content</h4>
                    </div>

                    <div class="md:col-span-2">
                        <label class="form-label">Title</label>
                        <input type="text" id="r_title" class="form-control font-bold" required>
                    </div>

                    <div class="md:col-span-2">
                        <label class="form-label">Aim</label>
                        <textarea id="r_aim" rows="2" class="form-control"></textarea>
                    </div>

                    <div class="md:col-span-2">
                        <label class="form-label">Algorithm</label>
                        <textarea id="r_algorithm" rows="5" class="form-control bg-gray-50"></textarea>
                    </div>

                    <div class="md:col-span-2">
                        <label class="form-label">Program (C Code)</label>
                        <!-- Normal Editor -->
                        <textarea id="r_program" rows="12" class="form-control font-mono bg-gray-50"></textarea>
                        <!-- Highlight Display (Hidden by default) -->
                        <div id="r_program_display" class="hidden form-control font-mono bg-gray-50"></div>
                    </div>

                    <div class="md:col-span-2">
                        <label class="form-label">Output</label>
                        <textarea id="r_output" rows="6" class="form-control turbo-c-terminal"></textarea>
                    </div>

                    <!-- AI Code Checker UI -->
                    <div class="md:col-span-2 bg-yellow-50 border border-yellow-300 p-4 rounded mb-2 shadow-sm">
                        <div class="flex justify-between items-center">
                            <div>
                                <h5 class="font-bold text-yellow-800 m-0"><i class="fa-solid fa-triangle-exclamation"></i> AI Code Checker</h5>
                                <p class="text-xs text-yellow-700 m-0">Checks syntax, dependencies, and formatting logically.</p>
                            </div>
                            <button type="button" id="checkCodeBtn" class="btn btn-warning btn-sm text-yellow-900 border-yellow-500">
                                Check Mistakes <div id="checkSpinner" class="spinner spinner-dark hidden ml-2"></div>
                            </button>
                        </div>
                        
                        <div id="errorCheckResult" class="hidden mt-4 bg-white p-4 border border-yellow-200 rounded text-sm shadow-inner">
                            <div id="errorMsg" class="mb-4"></div>
                            <div class="flex gap-3 pt-3 border-t border-gray-200">
                                <button type="button" id="ignoreErrorBtn" class="btn btn-secondary btn-sm">Leave it (Ignore)</button>
                                <button type="button" id="modifyErrorBtn" class="btn btn-success btn-sm"><i class="fa-solid fa-wrench"></i> Modify & Fix</button>
                            </div>
                        </div>
                    </div>

                    <div class="md:col-span-2">
                        <label class="form-label">Result</label>
                        <textarea id="r_result" rows="2" class="form-control"></textarea>
                    </div>

                    <div class="md:col-span-2 flex justify-end gap-3 mt-4 pt-4 border-t border-gray-200">
                        <button type="button" id="saveDbBtn" class="btn btn-success"><i class="fa-solid fa-save"></i> Save Record</button>
                        <button type="button" id="generatePdfBtn" class="btn btn-primary"><i class="fa-solid fa-file-pdf"></i> Download PDF <div id="pdfSpinner" class="spinner hidden"></div></button>
                    </div>
                </form>
            </div>
        </section>
    </main>

    <script>
        // --- API KEY Settings Modal Logic ---
        const settingsModal = document.getElementById('settingsModal');
        const apiKeyInput = document.getElementById('apiKeyInput');

        document.getElementById('openSettingsBtn').addEventListener('click', () => {
            apiKeyInput.value = localStorage.getItem('geminiApiKey') || '';
            settingsModal.classList.remove('hidden');
        });

        document.getElementById('closeSettingsBtn').addEventListener('click', () => {
            settingsModal.classList.add('hidden');
        });

        document.getElementById('saveSettingsBtn').addEventListener('click', () => {
            const key = apiKeyInput.value.trim();
            localStorage.setItem('geminiApiKey', key);
            settingsModal.classList.add('hidden');
            alert('API Key Saved Successfully!');
        });

        function getAuthHeaders() {
            return {
                'Content-Type': 'application/json',
                'X-API-Key': localStorage.getItem('geminiApiKey') || ''
            };
        }


        // --- App Navigation & Logic ---
        const sections = { dash: document.getElementById('dashboardSection'), editor: document.getElementById('editorSection') };
        let loadedExperiments = [];
        let fixedProgramCache = "";
        let fixedOutputCache = "";

        function showSection(sectionId) {
            Object.values(sections).forEach(s => s.classList.add('hidden'));
            sections[sectionId].classList.remove('hidden');
        }

        function loadSavedStudentData() {
            const savedName = localStorage.getItem('studentName');
            const savedReg = localStorage.getItem('studentRegNo');
            if(savedName) document.getElementById('r_name').value = savedName;
            if(savedReg) document.getElementById('r_regno').value = savedReg;
        }

        function saveStudentDataLocally() {
            localStorage.setItem('studentName', document.getElementById('r_name').value);
            localStorage.setItem('studentRegNo', document.getElementById('r_regno').value);
        }

        function escapeHTML(str) {
            return str.replace(/[&<>'"]/g, tag => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
            }[tag] || tag));
        }

        document.getElementById('createNewBtn').addEventListener('click', () => {
            document.getElementById('recordForm').reset();
            document.getElementById('r_id').value = ''; 
            loadSavedStudentData(); 
            showSection('editor');
        });

        document.getElementById('backToDashBtn').addEventListener('click', () => showSection('dash'));

        async function loadExperiments() {
            const listEl = document.getElementById('experimentsList');
            const noMsg = document.getElementById('noRecordsMsg');
            listEl.innerHTML = '';
            
            try {
                const response = await fetch('/api/experiments');
                loadedExperiments = await response.json();

                if (loadedExperiments.length === 0) {
                    noMsg.classList.remove('hidden');
                } else {
                    noMsg.classList.add('hidden');
                    loadedExperiments.forEach((data, index) => {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td><strong>${data.expNo || '-'}</strong></td>
                            <td>${data.title || 'Untitled'}</td>
                            <td>${data.date || '-'}</td>
                            <td class="text-right">
                                <button class="btn-text mr-3" onclick='window.loadRecordIntoEditor(${index})'>Edit</button>
                                <button class="btn-text-danger" onclick='deleteRecord(${data.id})'>Delete</button>
                            </td>
                        `;
                        listEl.appendChild(tr);
                    });
                }
            } catch (err) {}
        }

        window.deleteRecord = async function(id) {
            if(confirm("Are you sure you want to delete this record?")) {
                await fetch(`/api/experiments/${id}`, { method: 'DELETE' });
                loadExperiments();
            }
        }

        window.loadRecordIntoEditor = function(index) {
            const data = loadedExperiments[index];
            if(!data) return;
            document.getElementById('r_id').value = data.id || '';
            document.getElementById('r_name').value = data.name || '';
            document.getElementById('r_regno').value = data.regNo || '';
            document.getElementById('r_expno').value = data.expNo || '';
            document.getElementById('r_date').value = data.date || '';
            document.getElementById('r_title').value = data.title || '';
            document.getElementById('r_aim').value = data.aim || '';
            document.getElementById('r_algorithm').value = data.algorithm || '';
            document.getElementById('r_program').value = data.program || '';
            document.getElementById('r_output').value = data.output || '';
            document.getElementById('r_result').value = data.result || '';
            
            document.getElementById('errorCheckResult').classList.add('hidden');
            document.getElementById('r_program_display').classList.add('hidden');
            document.getElementById('r_program').classList.remove('hidden');
            showSection('editor');
        }

        loadExperiments();

        // --- ERROR CHECKER API ---
        document.getElementById('checkCodeBtn').addEventListener('click', async () => {
            const program = document.getElementById('r_program').value;
            const output = document.getElementById('r_output').value;
            
            if(!program.trim()) return alert("Please input or scan a program first.");

            const btn = document.getElementById('checkCodeBtn');
            const spinner = document.getElementById('checkSpinner');
            btn.disabled = true;
            spinner.classList.remove('hidden');

            try {
                const response = await fetch('/api/check-code', {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({ program, output })
                });

                if (!response.ok) {
                    const errTxt = await response.text();
                    throw new Error(errTxt.includes("API Key") ? "Invalid or missing API Key in settings." : errTxt);
                }
                const data = await response.json();

                document.getElementById('errorCheckResult').classList.remove('hidden');
                const msgEl = document.getElementById('errorMsg');

                if (data.has_error) {
                    const errorLineNum = data.error_line_number || 0;
                    msgEl.innerHTML = `
                        <div class="text-red-600 mb-2 text-lg"><i class="fa-solid fa-xmark-circle"></i> <b>Mistake Found (Line ${errorLineNum}):</b></div>
                        <div class="mb-3 text-gray-700 font-semibold">${data.explanation}</div>
                        <div class="mb-1 text-xs text-gray-500 font-bold uppercase tracking-wider">Line ${errorLineNum} (Wrong):</div>
                        <div class="bg-red-50 p-2 text-red-800 font-mono text-sm rounded mb-2 border border-red-200"><del>${escapeHTML(data.wrong_line_code || '')}</del></div>
                        <div class="mb-1 text-xs text-gray-500 font-bold uppercase tracking-wider">Corrected Version:</div>
                        <div class="bg-green-50 p-2 text-green-800 font-mono text-sm rounded mb-2 border border-green-200"><ins>${escapeHTML(data.corrected_line_code || '')}</ins></div>
                    `;
                    
                    if (errorLineNum > 0) {
                        const lines = program.split('\\n');
                        let highlightedHTML = '';
                        lines.forEach((line, idx) => {
                            const escapedLine = escapeHTML(line) || ' ';
                            if (idx + 1 === errorLineNum) {
                                highlightedHTML += `<div class="error-line-highlight">${escapedLine}</div>`;
                            } else {
                                highlightedHTML += `<div>${escapedLine}</div>`;
                            }
                        });
                        
                        document.getElementById('r_program').classList.add('hidden');
                        const displayEl = document.getElementById('r_program_display');
                        displayEl.innerHTML = highlightedHTML;
                        displayEl.classList.remove('hidden');
                    }

                } else {
                    msgEl.innerHTML = `<div class="text-green-600 text-lg"><i class="fa-solid fa-check-circle"></i> <b>All Good!</b></div>
                                       <div class="text-gray-600 mt-1">No errors found. Output formatting and syntax look correct.</div>`;
                    document.getElementById('r_program_display').classList.add('hidden');
                    document.getElementById('r_program').classList.remove('hidden');
                }

                fixedProgramCache = data.fixed_program || program;
                fixedOutputCache = data.fixed_output || output;

            } catch (e) {
                alert("Error: " + e.message);
            } finally {
                btn.disabled = false;
                spinner.classList.add('hidden');
            }
        });

        document.getElementById('ignoreErrorBtn').addEventListener('click', () => {
            document.getElementById('errorCheckResult').classList.add('hidden');
            document.getElementById('r_program_display').classList.add('hidden');
            document.getElementById('r_program').classList.remove('hidden');
        });

        document.getElementById('modifyErrorBtn').addEventListener('click', () => {
            document.getElementById('r_program').value = fixedProgramCache;
            document.getElementById('r_output').value = fixedOutputCache;
            document.getElementById('errorCheckResult').classList.add('hidden');
            document.getElementById('r_program_display').classList.add('hidden');
            document.getElementById('r_program').classList.remove('hidden');
            alert('Your program and output were automatically corrected!');
        });


        // --- SAVE TO SQLITE DB ---
        document.getElementById('saveDbBtn').addEventListener('click', async () => {
            if (!document.getElementById('recordForm').checkValidity()) return document.getElementById('recordForm').reportValidity();
            saveStudentDataLocally(); 

            const btn = document.getElementById('saveDbBtn');
            const originalHTML = btn.innerHTML;
            btn.innerHTML = 'Saving...';
            btn.disabled = true;

            try {
                const response = await fetch('/api/experiments', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(getFormData())
                });
                if (response.ok) { alert('Record saved to Database successfully.'); loadExperiments(); }
            } catch (e) { alert("Error saving: " + e.message); } 
            finally { btn.innerHTML = originalHTML; btn.disabled = false; }
        });

        // --- GEMINI MULTI-IMAGE EXTRACT ---
        document.getElementById('scanBtn').addEventListener('click', async () => {
            const fileInput = document.getElementById('imageUpload');
            const files = fileInput.files;

            if (files.length === 0) return alert('Please select at least 1 image.');
            if (files.length > 5) return alert('You can only upload a maximum of 5 images limit.');

            const btn = document.getElementById('scanBtn');
            const spinner = document.getElementById('scanSpinner');
            btn.disabled = true;
            spinner.classList.remove('hidden');

            try {
                const base64Promises = Array.from(files).map(file => {
                    return new Promise((resolve) => {
                        const reader = new FileReader();
                        reader.onload = () => resolve(reader.result);
                        reader.readAsDataURL(file);
                    });
                });

                const base64Images = await Promise.all(base64Promises);
                
                const response = await fetch('/api/extract', {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({ images: base64Images })
                });
                
                if (!response.ok) {
                    const errTxt = await response.text();
                    throw new Error(errTxt.includes("API Key") ? "Invalid or missing API Key in settings." : errTxt);
                }
                const data = await response.json();
                
                if(data.name) document.getElementById('r_name').value = data.name;
                if(data.regNo) document.getElementById('r_regno').value = data.regNo;
                saveStudentDataLocally(); 

                if(data.expNo) document.getElementById('r_expno').value = data.expNo;
                if(data.title) document.getElementById('r_title').value = data.title;
                if(data.aim) document.getElementById('r_aim').value = data.aim;
                if(data.algorithm) document.getElementById('r_algorithm').value = data.algorithm;
                if(data.program) document.getElementById('r_program').value = data.program;
                if(data.output) document.getElementById('r_output').value = data.output;
                if(data.result) document.getElementById('r_result').value = data.result;

            } catch (error) {
                alert("Extraction failed: " + error.message);
            } finally {
                btn.disabled = false;
                spinner.classList.add('hidden');
                fileInput.value = '';
            }
        });

        function getFormData() {
            return {
                id: document.getElementById('r_id').value,
                name: document.getElementById('r_name').value,
                regNo: document.getElementById('r_regno').value,
                expNo: document.getElementById('r_expno').value,
                date: document.getElementById('r_date').value,
                title: document.getElementById('r_title').value,
                aim: document.getElementById('r_aim').value,
                algorithm: document.getElementById('r_algorithm').value,
                program: document.getElementById('r_program').value,
                output: document.getElementById('r_output').value,
                result: document.getElementById('r_result').value
            };
        }

        // --- PDF GENERATION FOR PYINSTALLER NATIVE APPS ---
        document.getElementById('generatePdfBtn').addEventListener('click', async () => {
            if (!document.getElementById('recordForm').checkValidity()) return document.getElementById('recordForm').reportValidity();
            saveStudentDataLocally(); 

            const btn = document.getElementById('generatePdfBtn');
            const spinner = document.getElementById('pdfSpinner');
            btn.disabled = true;
            spinner.classList.remove('hidden');

            const data = getFormData();

            try {
                const response = await fetch('/api/generate-pdf', {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify(data)
                });

                if (!response.ok) throw new Error(await response.text() || "Server error 500");
                const resultData = await response.json();

                // To bypass Pyinstaller blob restrictions, backend sends Base64 directly
                if(resultData.pdf_base64) {
                    const a = document.createElement('a');
                    a.style.display = 'none';
                    a.href = "data:application/pdf;base64," + resultData.pdf_base64;
                    a.download = `Exp_${data.expNo}_${data.regNo}.pdf`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                } else {
                    throw new Error("Failed to generate PDF Base64.");
                }
                
            } catch (error) { alert("PDF Generation error: " + error.message); } 
            finally { btn.disabled = false; spinner.classList.add('hidden'); }
        });
    </script>
</body>
</html>
"""

# --- Database Initialization ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, regNo TEXT, expNo TEXT, date TEXT,
            title TEXT, aim TEXT, algorithm TEXT, program TEXT,
            output TEXT, result TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row 
    return conn

init_db()

def safe_text(text):
    if not text: return ""
    return html.escape(str(text)).replace('\n', '<br/>')

# --- UI Route ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

# --- API Endpoints ---
@app.route('/api/experiments', methods=['GET'])
def get_experiments():
    conn = get_db_connection()
    experiments = conn.execute('SELECT * FROM experiments ORDER BY created_at DESC').fetchall()
    conn.close()
    return jsonify([dict(row) for row in experiments])

@app.route('/api/experiments', methods=['POST'])
def save_experiment():
    data = request.json
    conn = get_db_connection()
    c = conn.cursor()
    
    if data.get('id'):
        c.execute('''
            UPDATE experiments SET name=?, regNo=?, expNo=?, date=?, title=?, aim=?, algorithm=?, program=?, output=?, result=?
            WHERE id=?
        ''', (data['name'], data['regNo'], data['expNo'], data['date'], data['title'], 
              data['aim'], data['algorithm'], data['program'], data['output'], data['result'], data['id']))
    else:
        c.execute('''
            INSERT INTO experiments (name, regNo, expNo, date, title, aim, algorithm, program, output, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data['name'], data['regNo'], data['expNo'], data['date'], data['title'], 
              data['aim'], data['algorithm'], data['program'], data['output'], data['result']))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/experiments/<int:id>', methods=['DELETE'])
def delete_experiment(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM experiments WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/check-code', methods=['POST'])
def check_code():
    try:
        client = get_gemini_client()
        data = request.json
        program = data.get('program', '')
        output = data.get('output', '')

        prompt = f"""
        Analyze the following C program and its intended output.
        Find any syntax or logical errors. Provide a SIMPLE, short, and easily understandable explanation.

        CRITICAL RULES:
        1. Ensure `#include <stdio.h>`, `#include <conio.h>`, `clrscr();`, and `getch();` are present in the correct places. Add them if missing.
        2. In `printf`, DO NOT put a blank space immediately after `\\n` or `\\t`. Example: `printf("\\n Enter");` MUST become `printf("\\nEnter");`.
        3. The fixed output MUST NOT contain the word 'OUTPUT:'. 
        4. Do not add extra blank new lines or comment lines in the program.
        5. The output must perfectly match the code's output exactly (no missing spaces, dots, or formatting changes).
        6. Identify the EXACT line number (1-indexed) where the primary error occurs in the original program.

        Respond in valid JSON strictly following this schema:
        {{
            "has_error": true/false,
            "error_line_number": 5, 
            "wrong_line_code": "printf(\"\\\\n Enter name:\");",
            "corrected_line_code": "printf(\"\\\\nEnter name:\");",
            "explanation": "Removed space after \\\\n and added missing clrscr();.",
            "fixed_program": "Complete fixed C code here",
            "fixed_output": "Exact matched output text here"
        }}
        
        Program:
        {program}
        
        Output:
        {output}
        """

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt]
        )
        
        response_text = response.text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:-3]
        elif response_text.startswith("```"):
            response_text = response_text[3:-3]
            
        return jsonify(json.loads(response_text))
        
    except ValueError as ve:
        return str(ve), 401
    except Exception as e:
        print("Code Check Error:", e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/extract', methods=['POST'])
def extract_text():
    try:
        client = get_gemini_client()
        data = request.json
        images_base64_list = data.get('images', [])
        
        if not images_base64_list:
            return jsonify({"error": "No images provided"}), 400

        prompt = """
        You are an expert OCR and data extraction system. I am providing you with up to 5 images of a single C Programming Lab Record.
        First, determine the chronological order of the pages. Then extract the complete content.
        CRITICAL RULES:
        1. In the 'program' section, ensure `#include <conio.h>`, `clrscr();`, and `getch();` exist. If not, add them automatically.
        2. In the 'program' section, for `printf` statements, DO NOT put a blank space immediately after `\\n` or `\\t`. (e.g. `printf("\\n Enter");` must be extracted/fixed as `printf("\\nEnter");`).
        3. In the 'output' section, DO NOT include the word "OUTPUT:" or any variations as a heading. Give only the raw text of the output.
        Strictly format the output as a JSON object with exactly these keys:
        "name", "regNo", "expNo", "title", "aim", "algorithm", "program", "output", "result".
        If a section is missing, return an empty string for that key. Ensure the C code remains formatted perfectly.
        Do not include markdown formatting like ```json in the response, just return the raw JSON string.
        """
        
        contents_list = [prompt]
        
        for img_b64 in images_base64_list:
            if ',' in img_b64:
                img_b64 = img_b64.split(',')[1]
            img_bytes = base64.b64decode(img_b64)
            contents_list.append(types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg'))
            
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents_list
        )
        
        response_text = response.text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:-3]
        elif response_text.startswith("```"):
            response_text = response_text[3:-3]
            
        return jsonify(json.loads(response_text))

    except ValueError as ve:
        return str(ve), 401
    except Exception as e:
        print("Extraction Error:", e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/generate-pdf', methods=['POST'])
def generate_pdf():
    try:
        data = request.json
        buffer = BytesIO()
        
        margin_x = 0.5 * inch
        margin_top = 0.8 * inch 
        margin_bottom = 0.8 * inch 

        doc = SimpleDocTemplate(
            buffer, pagesize=A4, rightMargin=margin_x + 0.1*inch, leftMargin=margin_x + 0.1*inch, 
            topMargin=margin_top, bottomMargin=margin_bottom
        )
        
        def draw_decorations(canvas_obj, document):
            canvas_obj.saveState()
            
            # 1. NAME AND REG NO
            canvas_obj.setFont('Helvetica-Bold', 10.5)
            canvas_obj.drawString(margin_x, A4[1] - 0.5 * inch, f"Name : {data.get('name', '')}")
            canvas_obj.drawRightString(A4[0] - margin_x, A4[1] - 0.5 * inch, f"Reg no : {data.get('regNo', '')}")

            # 2. BLACK PAGE BORDER
            canvas_obj.setStrokeColor(colors.black)
            canvas_obj.setLineWidth(1)
            box_bottom_y = 0.7 * inch
            box_height = A4[1] - 0.65 * inch - box_bottom_y
            canvas_obj.rect(margin_x, box_bottom_y, A4[0] - 2*margin_x, box_height)
            
            # 3. FOOTER
            canvas_obj.setFont('Helvetica', 10)
            canvas_obj.drawRightString(A4[0] - margin_x, 0.45 * inch, "22UCS202 - C Programming")

            # 4. PPTX-STYLE WATERMARK
            if os.path.exists(LOGO_PATH):
                canvas_obj.setFillAlpha(0.1) 
                logo_size = 5.0 * inch
                center_x = (A4[0] - logo_size) / 2
                center_y = (A4[1] - logo_size) / 2
                canvas_obj.drawImage(LOGO_PATH, center_x, center_y, width=logo_size, height=logo_size, mask='auto')

            canvas_obj.restoreState()

        styles = getSampleStyleSheet()
        
        table_left = ParagraphStyle('TLeft', fontName='Helvetica-Bold', fontSize=10.5, leading=16)
        table_center = ParagraphStyle('TCenter', fontName='Helvetica-Bold', fontSize=12, alignment=1, textTransform='uppercase', leading=18)
        
        heading_style = ParagraphStyle(
            'Heading', parent=styles['Heading2'], fontSize=12, 
            spaceAfter=12, spaceBefore=18, textColor=colors.black, textTransform='uppercase'
        )
        body_style = ParagraphStyle(
            'Body', parent=styles['Normal'], fontSize=11, 
            spaceAfter=12, leading=22 
        )
        
        # TURBO C STYLE WITH 5PX PADDING INSIDE
        turbo_c_style = ParagraphStyle(
            'TurboC', parent=styles['Normal'], fontName='Courier-Bold', fontSize=10.5, leading=16,
            textColor=colors.HexColor("#cccccc"), backColor=colors.black, 
            borderPadding=5, 
            spaceAfter=20, spaceBefore=10, 
            leftIndent=5, rightIndent=5
        )

        story = []

        date_str = safe_text(data.get('date', '')).strip()
        if not date_str:
            date_str = "&nbsp;" * 10 

        header_data = [
            [Paragraph(f"Exp.no : {safe_text(data.get('expNo', ''))}", table_left), Paragraph(safe_text(data.get('title', 'EXPERIMENT')), table_center)],
            [Paragraph(f"Date &nbsp;&nbsp;&nbsp;: {date_str}", table_left), ""]
        ]
        
        total_available_width = A4[0] - (2 * margin_x) - (0.2 * inch)
        col1_width = 1.5 * inch
        col2_width = total_available_width - col1_width

        header_table = Table(header_data, colWidths=[col1_width, col2_width])
        header_table.setStyle(TableStyle([
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('SPAN', (1, 0), (1, 1)), 
            ('ALIGN', (1,0), (1,1), 'CENTER'),
            ('VALIGN', (1,0), (1,1), 'MIDDLE'),
            ('VALIGN', (0,0), (0,1), 'MIDDLE'),
            ('LEFTPADDING', (0,0), (0,1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        
        story.append(header_table)
        story.append(Spacer(1, 0.2 * inch))

        # --- ALGORITHM PROCESSOR ---
        alg_raw = data.get('algorithm', '')
        processed_lines = []
        for line in alg_raw.split('\n'):
            line = line.strip()
            if not line: continue
            
            line_clean = re.sub(r'^step\s*\d+\s*[:\-\.]\s*', '', line, flags=re.IGNORECASE).strip()
            
            lower_line = line_clean.lower()
            if "start the program" in lower_line or "stop the program" in lower_line:
                continue
                
            if line_clean and line_clean not in processed_lines:
                processed_lines.append(line_clean)
                
        alg_html = "Step 1: Start the program<br/>"
        for i, line in enumerate(processed_lines):
            alg_html += f"Step {i+2}: {html.escape(line)}<br/>"
        alg_html += f"Step {len(processed_lines)+2}: Stop the program"


        # --- INJECT SECTIONS ---
        if data.get('aim'):
            story.append(Paragraph("<b>AIM :</b>", heading_style))
            story.append(Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;" + safe_text(data.get('aim')), body_style))

        if alg_html:
            story.append(Paragraph("<b>ALGORITHM :</b>", heading_style))
            story.append(Paragraph(alg_html, body_style))

        if data.get('program'):
            program_elements = [
                Paragraph("<b>PROGRAM :</b>", heading_style),
                Paragraph(safe_text(data.get('program')), body_style)
            ]
            
            prog_lines = len(data.get('program', '').split('\n'))
            if prog_lines < 15:
                story.append(KeepTogether(program_elements))
            else:
                for el in program_elements: story.append(el)


        # Output and Result blocks
        if data.get('output'):
            out_raw = data.get('output', '').strip()
            out_clean = re.sub(r'^\s*output\s*[:\-\.]*\s*', '', out_raw, flags=re.IGNORECASE)
            story.append(Paragraph("<b>OUTPUT :</b>", heading_style))
            story.append(Paragraph(safe_text(out_clean), turbo_c_style))

        if data.get('result'):
            result_block = [
                Paragraph("<b>RESULT :</b>", heading_style),
                Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;" + safe_text(data.get('result')), body_style)
            ]
            
            # This spacer mathematically ensures everything after it touches the absolute bottom
            story.append(PushToBottomSpacer(result_block))
            
            # Also keep together to be perfectly safe
            story.append(KeepTogether(result_block))

        # Build PDF & Return Base64
        doc.build(story, onFirstPage=draw_decorations, onLaterPages=draw_decorations)
        pdf_bytes = buffer.getvalue()
        
        # We send it as base64 to completely sidestep Pyinstaller Webview blob limitations
        pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
        
        return jsonify({
            "success": True, 
            "pdf_base64": pdf_base64
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)


