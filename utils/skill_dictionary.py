"""Dynamic skill dictionaries for resume/job parsing heuristics.

Exposes get_skill_terms() which returns a cached merged set of skills drawn from:
  1. Static fallback list (broad cross‑industry coverage)
  2. Optional O*NET enrichment (when credentials present)

Environment variables:
  ONET_USER / ONET_PASSWORD   -> enable O*NET enrichment
  ONET_SKILL_CODES            -> comma separated SOC codes to query (defaults provided)
"""

import logging
import os
from functools import lru_cache
from typing import List

from utils import onet_client

logger = logging.getLogger(__name__)

# Fallback list kept for environments without O*NET credentials.
_FALLBACK_SKILL_TERMS = [
    # Software engineering & programming languages
    'python', 'java', 'javascript', 'typescript', 'node.js', 'node', 'react', 'angular', 'vue',
    'c', 'c#', '.net', 'c++', 'go', 'rust', 'php', 'ruby', 'scala', 'swift', 'objective-c', 'perl',
    'sql', 'postgresql', 'mysql', 'mariadb', 'sqlite', 'mongodb', 'cassandra', 'couchbase', 'redis',
    'graphql', 'rest api', 'grpc', 'soap', 'xml', 'json',
    'spring', 'spring boot', 'hibernate', 'django', 'flask', 'fastapi', 'express', 'laravel', 'rails',
    'next.js', 'nuxt', 'svelte', 'alpine.js', 'jquery', 'backbone',
    'android', 'ios', 'kotlin', 'flutter', 'react native', 'xamarin', 'cordova',
    'unity', 'unreal engine', 'game development',

    # Cloud & infrastructure
    'aws', 'azure', 'gcp', 'cloudformation', 'terraform', 'pulumi', 'ansible', 'chef', 'puppet',
    'kubernetes', 'docker', 'openshift', 'helm', 'istio', 'linkerd',
    'serverless', 'lambda', 'faas', 'cloud run', 'cloud functions', 'app service',
    'sre', 'devops', 'infrastructure as code', 'observability', 'prometheus', 'grafana', 'datadog', 'splunk', 'new relic',
    'elastic stack', 'elk', 'logstash', 'kibana', 'filebeat',
    'network security', 'firewalls', 'vpn', 'load balancing', 'dns', 'tcp/ip',

    # Data engineering, analytics, ai/ml
    'data engineering', 'data analytics', 'data science', 'etl', 'elt', 'data warehousing', 'data lakes',
    'hadoop', 'spark', 'hive', 'pig', 'impala', 'flink', 'beam',
    'airflow', 'dbt', 'informatica', 'talend', 'pentaho', 'ssis', 'ssas', 'ssrs',
    'power bi', 'tableau', 'looker', 'qlik', 'mode analytics',
    'machine learning', 'deep learning', 'mlops', 'nlp', 'natural language processing', 'computer vision',
    'pandas', 'numpy', 'scikit-learn', 'tensorflow', 'keras', 'pytorch', 'hugging face', 'xgboost',
    'statistics', 'bayesian inference', 'predictive modeling', 'time series', 'reinforcement learning',
    'bigquery', 'redshift', 'snowflake', 'synapse', 'databricks', 'athena', 'presto',

    # Cybersecurity & compliance
    'cybersecurity', 'penetration testing', 'ethical hacking', 'threat modeling', 'incident response',
    'iam', 'identity and access management', 'zero trust', 'siem', 'soar',
    'owasp', 'nist', 'iso 27001', 'gdpr', 'hipaa', 'pci dss', 'soc 2',
    'malware analysis', 'digital forensics', 'vulnerability management', 'security operations',

    # QA, testing, automation
    'quality assurance', 'qa automation', 'test automation', 'unit testing', 'integration testing', 'system testing',
    'tdd', 'bdd', 'selenium', 'cypress', 'playwright', 'webdriverio', 'pytest', 'robot framework',
    'jmeter', 'loadrunner', 'gatling', 'performance testing', 'chaos engineering',

    # Product & project management
    'product management', 'product owner', 'scrum master', 'agile', 'scrum', 'kanban', 'lean',
    'jira', 'confluence', 'trello', 'monday.com',
    'roadmapping', 'stakeholder management', 'user stories', 'prioritisation', 'backlog grooming',
    'project management', 'pmp', 'prince2', 'waterfall', 'risk management', 'budgeting',

    # UX/UI & creative
    'user experience', 'user interface design', 'ux research', 'wireframing', 'prototyping',
    'figma', 'sketch', 'adobe xd', 'invision', 'balsamiq', 'axure', 'adobe photoshop', 'illustrator',
    'design systems', 'accessibility', 'human-centered design', 'design thinking', 'usability testing',

    # Business, finance, operations
    'business analysis', 'requirements gathering', 'process improvement', 'six sigma', 'lean six sigma',
    'finance', 'accounting', 'fp&a', 'financial modeling', 'valuation', 'ifrs', 'gaap', 'tax',
    'auditing', 'internal controls', 'sox compliance',
    'supply chain', 'logistics', 'procurement', 'inventory management', 'erp', 'sap', 'oracle ebs', 'netsuite',
    'customer success', 'crm', 'salesforce', 'hubspot', 'zendesk',

    # Marketing & communications
    'digital marketing', 'seo', 'sem', 'ppc', 'content marketing', 'email marketing', 'marketing automation',
    'google analytics', 'google ads', 'facebook ads', 'linkedin ads', 'campaign management',
    'brand strategy', 'public relations', 'copywriting', 'social media', 'community management',
    'market research', 'customer journey', 'growth marketing', 'ab testing',

    # Human resources & people operations
    'talent acquisition', 'recruiting', 'hris', 'workday', 'successfactors', 'bamboohr',
    'employee relations', 'performance management', 'compensation', 'benefits administration', 'payroll',
    'learning and development', 'organizational development', 'change management',

    # Healthcare & life sciences
    'clinical research', 'gcp', 'gmp', 'glp', 'fda compliance',
    'electronic medical records', 'epic', 'cerner', 'hl7', 'hipaa compliance',
    'nursing', 'patient care', 'telehealth', 'medical coding', 'icd-10', 'cpt',
    'biostatistics', 'bioinformatics', 'pharmacovigilance', 'clinical trials',

    # Engineering disciplines
    'mechanical engineering', 'electrical engineering', 'civil engineering', 'chemical engineering',
    'autocad', 'solidworks', 'revit', 'catia', 'ansys',
    'manufacturing', 'process engineering', 'quality engineering', 'lean manufacturing', 'root cause analysis',
    'maintenance engineering', 'reliability engineering', 'hvac', 'plc', 'scada',

    # Energy & environment
    'renewable energy', 'solar pv', 'wind energy', 'battery storage', 'energy modeling',
    'oil and gas', 'pipeline', 'downstream', 'upstream', 'petroleum engineering',
    'environmental compliance', 'iso 14001', 'sustainability', 'esg reporting',

    # Legal & compliance
    'legal research', 'contracts', 'negotiation', 'intellectual property', 'patents', 'trademarks',
    'corporate governance', 'compliance', 'regulatory affairs', 'litigation',

    # Education & training
    'curriculum development', 'instructional design', 'learning management systems', 'moodle', 'blackboard',
    'classroom management', 'student assessment', 'e-learning', 'adult education',

    # Sales & customer-facing
    'sales strategy', 'account management', 'business development', 'lead generation', 'crm management',
    'customer relationship management', 'negotiation', 'sales forecasting', 'pipeline management',
    'customer success', 'customer retention', 'customer onboarding',

    # Soft skills & leadership
    'leadership', 'mentoring', 'coaching', 'team building', 'communication', 'presentation skills',
    'strategic planning', 'problem solving', 'critical thinking', 'analytical skills',
    'conflict resolution', 'time management', 'decision making', 'adaptability',

    # Emerging technologies & misc
    'blockchain', 'smart contracts', 'web3', 'metaverse', 'iot', 'edge computing',
    'robotics', 'rpa', 'chatbots', 'voice assistants', 'ar', 'vr', 'xr',
    'ethical ai', 'data privacy', 'explainable ai', 'digital twin',
]

DEFAULT_SKILL_CODES = [
    '15-1252.00',  # Software Developers
    '15-1241.00',  # Computer Network Architects
    '15-1245.00',  # Database Administrators and Architects
    '15-1221.00',  # Computer and Information Research Scientists
    '15-1212.00',  # Information Security Analysts
    '15-1232.00',  # Computer User Support Specialists
    '15-2041.00',  # Statisticians / Data Scientists
    '17-2112.00',  # Industrial Engineers
    '11-2021.00',  # Marketing Managers
    '11-1021.00',  # General and Operations Managers
    '13-1121.00',  # Meeting, Convention, and Event Planners
    '13-1161.00',  # Market Research Analysts and Marketing Specialists
    '29-1141.00',  # Registered Nurses
    '11-3121.00',  # Human Resources Managers
    '27-1021.00',  # Commercial and Industrial Designers
    '13-2011.00',  # Accountants and Auditors
]  # Base SOC codes spanning engineering, data, business, and operations.


def _skill_codes_from_env() -> List[str]:
    """Return SOC codes to fetch.

    Reads ONET_SKILL_CODES (comma separated). Falls back to DEFAULT_SKILL_CODES when unset or empty.
    Returns: list[str] of SOC codes.
    """
    raw = os.getenv('ONET_SKILL_CODES')
    if not raw:
        return DEFAULT_SKILL_CODES
    codes = [code.strip() for code in raw.split(',') if code.strip()]
    return codes or DEFAULT_SKILL_CODES


@lru_cache()
def load_skill_terms() -> List[str]:
    """Load & cache merged skill term list.

    Returns: Sorted list of lowercase skill terms. Cache invalidated only on process restart.
    Side effects: Logs counts and source (fallback vs O*NET merge).
    """
    base_terms = set(_FALLBACK_SKILL_TERMS)
    if onet_client.is_enabled():
        terms: List[str] = []
        codes = _skill_codes_from_env()
        for code in codes:
            for skill in onet_client.fetch_onet_skills(code):
                name = skill.get('skill') or skill.get('name')
                if name:
                    terms.append(name.lower())
        unique_terms = set(terms)
        if unique_terms:
            merged = sorted(base_terms | unique_terms)
            logger.info(
                'Loaded %s O*NET skill terms (merged with %s fallback terms) using codes: %s',
                len(unique_terms), len(base_terms), codes,
            )
            return merged
        logger.warning('O*NET credentials present but no skills returned; using fallback list only.')
        return sorted(base_terms)
    else:
        logger.info('O*NET credentials not detected; using fallback skill list.')
        return sorted(base_terms)


def get_skill_terms() -> List[str]:
    """Public accessor returning cached skill terms.

    Thin wrapper retained for semantic clarity in parser modules.
    """
    return load_skill_terms()


SECTION_PATTERNS = {
    # High level resume sections mapped to lists of heading variants (case‑insensitive matching).
    # Detected sections are used to populate the structured 'sections' object in resume parsing output.
    'SUMMARY': ['summary', 'professional summary', 'objective', 'profile'],
    'EXPERIENCE': ['experience', 'work experience', 'employment history', 'professional experience'],
    'EDUCATION': ['education', 'academic background', 'academics'],
    'SKILLS': ['skills', 'technical skills', 'core competencies'],
    'PROJECTS': ['projects', 'project experience', 'selected projects'],
    'CERTIFICATIONS': ['certifications', 'licenses', 'certificates'],
}

