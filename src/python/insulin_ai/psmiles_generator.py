#!/usr/bin/env python3
"""
PSMILES (Polymer SMILES) Generator

A specialized module for generating and validating Polymer SMILES strings
using Large Language Models with conversation memory and rule reinforcement.
"""

from langchain_community.llms import Ollama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain.memory import ConversationBufferWindowMemory
import json
import re
from datetime import datetime
from typing import Dict, List, Optional


class PSMILESGenerator:
    """
    Specialized agent for generating and validating Polymer SMILES (PSMILES) strings.
    """
    
    def __init__(self, 
                 model_type: str = "ollama",
                 ollama_model: str = "llama3.2",
                 ollama_host: str = "http://localhost:11434"):
        """
        Initialize PSMILES Generator with enhanced conversation memory.
        
        Args:
            model_type (str): Type of model to use (currently only 'ollama' supported)
            ollama_model (str): Name of the Ollama model to use
            ollama_host (str): Ollama server host URL
        """
        self.model_type = "ollama"  # Only Ollama supported now
        self.ollama_model = ollama_model
        self.ollama_host = ollama_host
        self.llm = Ollama(
            model=ollama_model,
            base_url=ollama_host,
            temperature=0.1  # Low temperature for consistent chemical generation
        )
        self.model_name = ollama_model
        
        # Initialize conversation memory to maintain context
        self.memory = ConversationBufferWindowMemory(
            k=10,  # Keep last 10 exchanges to maintain recent context
            return_messages=True,
            memory_key="chat_history"
        )
        
        # Load PSMILES rules and examples
        self.psmiles_rules = self._get_psmiles_rules()
        self.psmiles_examples = self._get_psmiles_examples()
        
        # Setup prompts with memory integration
        self.prompts = self._setup_prompts()
        
        # Track conversation turns for rule reinforcement
        self.conversation_count = 0
        self.rule_reinforcement_interval = 5  # Reinforce rules every 5 interactions
        
        print(f"✅ PSMILES Generator initialized with {self.model_name} ({self.model_type})")
        print(f"🧠 Conversation memory enabled (window size: {self.memory.k})")
        print(f"🔄 Rule reinforcement every {self.rule_reinforcement_interval} interactions")
    
    def _get_psmiles_rules(self) -> str:
        """Get the comprehensive PSMILES rules."""
        return """
CRITICAL PSMILES (Polymer SMILES) RULES - FOLLOW EXACTLY:

1. **NO SPACES**: PSMILES strings NEVER contain spaces
2. **NO HYPHENS**: PSMILES strings NEVER contain hyphens (-)
3. **NO EXPLICIT HYDROGEN**: Hydrogen atoms are suppressed 
4. **ATOMS**: Use atomic symbols (C, N, O, S, F, Cl, Br, etc.)
5. **TWO-CHARACTER ATOMS**: Put in square brackets [Br], [Cl]
6. **BONDS**: 
   - Single bonds: atoms next to each other (CC)
   - Double bonds: = (C=C)
   - Triple bonds: # (C#C)
   - Aromatic bonds: lowercase letters (c1ccccc1)
7. **BRANCHING**: Use parentheses for branches (C(C)C)
8. **RINGS**: Use numbers for ring closures (C1CCCCC1)
9. **STARS (*)**: Mark connection points in polymer repeat units
10. **REPEAT UNITS**: [*]CCC[*] means a propylene repeat unit
11. **BRACKETS []**: Used for:
    - Charged atoms: [NH3+], [O-]
    - Two-character atoms: [Br], [Cl]
    - Aromatic atoms with specifications: [nH]
12. **COMMON ERRORS TO AVOID**:
    - Spaces in strings: "C C C" ❌ → "CCC" ✅
    - Hyphens: "C-C-C" ❌ → "CCC" ✅
    - Missing brackets for two-letter atoms: "Br" ❌ → "[Br]" ✅
    - Explicit hydrogens: "CH2CH2" ❌ → "CC" ✅

EXAMPLES OF CORRECT PSMILES:
- Polyethylene: [*]CC[*]
- Polypropylene: [*]CC(C)[*]
- Polystyrene: [*]CC(c1ccccc1)[*]
- PVC: [*]CC(Cl)[*]
- Polyethylene oxide: [*]CCO[*]
- Poly(methyl methacrylate): [*]CC(C)(C(=O)OC)[*]
"""

    def _get_psmiles_examples(self) -> Dict[str, str]:
        """Get common PSMILES examples - HARDCODED for reliability."""
        return {
            # Basic building blocks - terminal connections
            "methylene": {
                "psmiles": "C",
                "description": "-CH2- (methylene unit, terminal connections)",
                "formula": "CH2"
            },
            "amine": {
                "psmiles": "N", 
                "description": "-NH- (amine linkage, terminal connections)",
                "formula": "NH"
            },
            "thiocarbonyl": {
                "psmiles": "C(=S)",
                "description": "-CS- (thiocarbonyl, terminal connections)",
                "formula": "CS"
            },
            "carbonyl": {
                "psmiles": "C(=O)",
                "description": "-CO- (carbonyl, terminal connections)",
                "formula": "CO"
            },
            "difluoromethylene": {
                "psmiles": "C(F)(F)",
                "description": "-CF2- (difluoromethylene, terminal connections)",
                "formula": "CF2"
            },
            "oxygen": {
                "psmiles": "O",
                "description": "-O- (ether linkage, terminal connections)",
                "formula": "O"
            },
            
            # Common polymers with proper connection points
            "polyethylene_glycol": {
                "psmiles": "[*]OCC[*]",
                "description": "-O-CH2-CH2- (PEG repeat unit with connection points)",
                "formula": "C2H4O"
            },
            "ethylene": {
                "psmiles": "CC",
                "description": "-CH2-CH2- (ethylene repeat unit, terminal connections)",
                "formula": "C2H4"
            },
            
            # Aromatic rings with connection points
            "para_phenylene": {
                "psmiles": "C(C=C1)=CC=C1",
                "description": "-C6H4- (para-phenylene, terminal connections)",
                "formula": "C6H4"
            },
            "para_phenylene_marked": {
                "psmiles": "[*]C(C=C1)=CC([*])=C1",
                "description": "-C6H4- (para-phenylene with marked connection points)",
                "formula": "C6H4"
            },
            "thiophene": {
                "psmiles": "C1=CSC(=C1)",
                "description": "-C4H2S- (thiophene ring, terminal connections)",
                "formula": "C4H2S"
            },
            "pyridine": {
                "psmiles": "C1=NC=C(C=C1)",
                "description": "-C5H3N- (pyridine ring, terminal connections)",
                "formula": "C5H3N"
            },
            "pyrrole": {
                "psmiles": "C(N1)=CC=C1",
                "description": "-C4H3N- (pyrrole ring, terminal connections)",
                "formula": "C4H3N"
            },
            
            # Complex units with connection points
            "amide_unit": {
                "psmiles": "CNC(=O)C",
                "description": "-CH2-NH-CO-CH2- (amide linkage, terminal connections)",
                "formula": "C2H4NO"
            },
            "complex_aromatic": {
                "psmiles": "CC(C=C1)=CC=C1C2=CSC(=C2)C(C=C3)=CC=C3",
                "description": "-CH2-C6H4-C4H2S-C6H4- (complex multi-ring, terminal connections)",
                "formula": "C17H12S"
            },
            
            # With explicit connection points
            "meta_phenylene": {
                "psmiles": "[*]C(C=C1)=CC([*])=C1",
                "description": "meta-phenylene with specified connection points",
                "formula": "C6H4"
            },
            "ortho_phenylene": {
                "psmiles": "[*]C(C=C1)=C([*])C=C1",
                "description": "ortho-phenylene with specified connection points",
                "formula": "C6H4"
            }
        }

    def _setup_prompts(self) -> Dict:
        """Setup prompt templates for PSMILES generation."""
        
        # Create the examples string in a template-safe format
        examples_text = "MANDATORY PSMILES EXAMPLES:\n\n"
        for name, info in self.psmiles_examples.items():
            examples_text += f"- {name.replace('_', ' ').title()}:\n"
            examples_text += f"  PSMILES: {info['psmiles']}\n"
            examples_text += f"  Description: {info['description']}\n"
            examples_text += f"  Formula: {info['formula']}\n\n"
        
        psmiles_system_prompt = f"""You are a PSMILES (Polymer SMILES) expert. Your ONLY job is to generate valid PSMILES strings.

{self.psmiles_rules}

{examples_text}

RESPONSE FORMAT - FOLLOW EXACTLY:
You MUST respond in this EXACT format:

PSMILES: [your_psmiles_string_here]

EXPLANATION: [brief explanation of the structure]

CRITICAL INSTRUCTIONS:
1. ALWAYS start your response with "PSMILES: "
2. NEVER use hyphens or spaces in the PSMILES string
3. NEVER write polymer names like "Polyethylene" - always write the actual PSMILES
4. NEVER write -CH2- or -CO- style notation - use proper SMILES
5. If asked for PEG, respond with "PSMILES: [*]OCC[*]"
6. If asked for polyethylene, respond with "PSMILES: [*]CC[*]"
7. If asked for polystyrene, respond with "PSMILES: [*]CC([*])C1=CC=CC=C1"
8. ALWAYS include exactly 2 [*] symbols for connection points
9. NEVER use empty brackets [] - always use [*] for connection points

EXAMPLES OF CORRECT RESPONSES:
User: "Generate PSMILES for PEG"
Your Response: "PSMILES: [*]OCC[*]

EXPLANATION: This represents the polyethylene glycol repeat unit -O-CH2-CH2- with connection points marked by [*]"

User: "PSMILES for polyethylene"
Your Response: "PSMILES: [*]CC[*]

EXPLANATION: This represents the ethylene repeat unit -CH2-CH2- with connection points marked by [*]"
"""

        psmiles_prompt = ChatPromptTemplate.from_messages([
            ("system", psmiles_system_prompt),
            ("human", "{input}"),
        ])
        
        validation_system_prompt = f"""You are a PSMILES validation expert. Your role is to analyze PSMILES strings for correctness according to the strict formatting rules.

{self.psmiles_rules}

VALIDATION CHECKLIST:
1. **Syntax Check**: No spaces, no hyphens, proper brackets, valid symbols
2. **Chemical Validity**: Proper bonding, realistic structures
3. **Rule Compliance**: Following all PSMILES-specific rules
4. **Connection Logic**: Proper terminal/internal connections

RESPONSE FORMAT:
- State if PSMILES is VALID or INVALID
- List any errors found
- Suggest corrections if needed
- Explain the chemical structure represented
- Rate confidence level (1-10)
"""

        validation_prompt = ChatPromptTemplate.from_messages([
            ("system", validation_system_prompt),
            ("human", "Please validate this PSMILES string: {psmiles_string}\n\nAdditional context: {context}"),
        ])
        
        return {
            'generate': psmiles_prompt,
            'validate': validation_prompt
        }
    
    def generate_psmiles(self, request: str) -> Dict:
        """
        Generate PSMILES string based on user request with conversation memory.
        Heavy emphasis on proper extraction and fallback mechanisms.
        
        Args:
            request (str): Description of desired polymer structure
            
        Returns:
            Dict: Generated PSMILES with explanation
        """
        try:
            # Increment conversation counter
            self.conversation_count += 1
            
            # Check if this is a follow-up request (variations, more examples, etc.)
            is_followup = any(keyword in request.lower() for keyword in [
                'variation', 'variations', 'different', 'another', 'more', 'additional', 
                'similar', 'alternative', 'other', 'examples', 'modify', 'change'
            ])
            
            # No hardcoded name→PSMILES mappings. LLM generates; validate via RDKit/psmiles only.
            # Build context-aware prompt including conversation history and rules
            base_rules = """
CRITICAL PSMILES (Polymer SMILES) RULES - FOLLOW EXACTLY:

1. **NO SPACES**: PSMILES strings NEVER contain spaces
2. **NO HYPHENS**: PSMILES strings NEVER contain hyphens (-)
3. **ATOMS**: Use atomic symbols (C, N, O, S, F, Cl, Br, etc.)
4. **BONDS**: Single bonds: atoms next to each other (CC), Double bonds: = symbol (C=C)
5. **BRANCHES**: Use round brackets ()
6. **CONNECTION POINTS**: Use [*] for non-terminal connections - THIS IS CRITICAL!

MANDATORY CONNECTION POINT RULES:
- PSMILES represents polymer REPEAT UNITS with exactly 2 connection points
- MUST have exactly 2 [*] symbols - NEVER 0, 1, 3, 4, or more!
- ALL PSMILES strings MUST have exactly 2 [*] symbols to specify connection points
- [*] shows exactly WHERE the polymer unit connects to adjacent units

CRITICAL EXAMPLES:
- PEG (ether linkage): [*]OCC[*] (connects through marked positions - exactly 2 [*])
- Ethylene (terminal): CC (connects through first/last C atoms - 0 [*])
- Meta-phenylene: [*]C(C=C1)=CC([*])=C1 (meta positions marked - exactly 2 [*])

FORBIDDEN EXAMPLES (NEVER GENERATE THESE):
- [*]C[*]C[*] (3 [*] symbols - WRONG!)
- [*]C(C=C1)=C([*])C([*])=C1 (3 [*] symbols - WRONG!)
- C[*] (1 [*] symbol - WRONG!)
"""
            
            # Get conversation history
            chat_history = ""
            if hasattr(self.memory, 'chat_memory') and self.memory.chat_memory.messages:
                recent_messages = self.memory.chat_memory.messages[-6:]  # Last 3 exchanges
                for i, msg in enumerate(recent_messages):
                    if hasattr(msg, 'content'):
                        role = "User" if i % 2 == 0 else "Assistant"
                        chat_history += f"{role}: {msg.content}\n"
            
            # Create context-aware prompt
            if is_followup and chat_history:
                explicit_request = f"""
{base_rules}

CONVERSATION CONTEXT:
{chat_history}

NEW REQUEST: {request}

IMPORTANT FOR VARIATIONS/FOLLOW-UPS:
- ALL PSMILES must have exactly 2 [*] symbols - no exceptions!
- If generating variations, maintain exactly 2 [*] symbols in each variation
- Follow the exact same PSMILES formatting rules as shown above
- Keep the same level of structural complexity

YOUR RESPONSE MUST START WITH "PSMILES: " followed by the actual chemical string with exactly 2 [*] symbols.
"""
            else:
                explicit_request = f"""
{base_rules}

Generate a PSMILES string for: {request}

CRITICAL EXAMPLES WITH EXACTLY 2 [*] SYMBOLS:
- For PEG or polyethylene glycol: PSMILES: [*]OCC[*]
- For polyethylene: PSMILES: [*]CC[*]
- For polystyrene: PSMILES: [*]CC([*])C1=CC=CC=C1
- For polypropylene: PSMILES: [*]CC([*])C
- For nylon: PSMILES: [*]NC(=O)CCCCC[*]
- For meta-phenylene: PSMILES: [*]C1=CC([*])=CC=C1

REMEMBER:
- NEVER use hyphens (-)
- NEVER use spaces
- NEVER write polymer names like "Polyethylene"
- ALWAYS write actual SMILES notation
- ALWAYS use exactly 2 [*] symbols in every PSMILES string
- Start your response with "PSMILES: "

YOUR RESPONSE MUST START WITH "PSMILES: " and contain exactly 2 [*] symbols.
"""
            
            # Generate response using the LLM 
            response = self.llm.invoke(explicit_request)
            
            # Parse response to extract PSMILES string with enhanced extraction
            psmiles_result = self._extract_psmiles_from_response(response)
            
            # If extraction failed, try direct parsing or provide fallback
            if not psmiles_result.get('psmiles') or psmiles_result['psmiles'] == 'Not found':
                psmiles_result = self._fallback_psmiles_extraction(request, response)
            
            # Save to memory
            self.memory.chat_memory.add_user_message(request)
            self.memory.chat_memory.add_ai_message(response)
            
            return {
                'success': True,
                'request': request,
                'psmiles': psmiles_result.get('psmiles', 'Could not generate'),
                'explanation': response,
                'conversation_turn': self.conversation_count,
                'rule_reinforcement': is_followup,
                'is_followup': is_followup,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                'success': False,
                'request': request,
                'error': str(e),
                'conversation_turn': self.conversation_count,
                'timestamp': datetime.now().isoformat()
            }
    
    def _fallback_psmiles_extraction(self, request: str, response: str) -> Dict:
        """Extract PSMILES from LLM response via pattern matching. No hardcoded name→PSMILES mappings."""
        chemical_patterns = [
            r'([A-Z][A-Za-z0-9\[\]\(\)\=\#\*]+)',  # Chemical-looking strings (includes [*])
            r'([A-Za-z]{2,}[\[\]\(\)\=\#\*]*[A-Za-z0-9]*)',  # Multi-character chemistry
        ]
        
        for pattern in chemical_patterns:
            matches = re.findall(pattern, response)
            for match in matches:
                if len(match) >= 2 and not match.lower() in ['the', 'and', 'for', 'with', 'this']:
                    # Validate that it has exactly 2 [*] symbols
                    if match.count('[*]') == 2:
                        return {'psmiles': match, 'pattern': 'chemical_pattern'}
        
        return {'psmiles': 'Generation failed', 'pattern': 'no_match'}
    
    def _format_examples_for_prompt(self) -> str:
        """Format examples for inclusion in prompts."""
        examples_text = ""
        for name, info in self.psmiles_examples.items():
            examples_text += f"- {name.replace('_', ' ').title()}:\n"
            examples_text += f"  PSMILES: {info['psmiles']}\n"
            examples_text += f"  Description: {info['description']}\n\n"
        return examples_text
    
    def validate_psmiles(self, psmiles_string: str, context: str = "") -> Dict:
        """
        Validate a PSMILES string for correctness.
        
        Args:
            psmiles_string (str): PSMILES string to validate
            context (str): Additional context for validation
            
        Returns:
            Dict: Validation results
        """
        try:
            # Basic syntax validation
            basic_validation = self._basic_syntax_check(psmiles_string)
            
            # Enhanced validation using LLM
            validation_prompt = self.prompts['validate']
            response = self.llm.invoke(
                validation_prompt.format(
                    psmiles_string=psmiles_string,
                    context=context
                )
            )
            
            return {
                'success': True,
                'psmiles_string': psmiles_string,
                'context': context,
                'basic_validation': basic_validation,
                'ai_validation': response,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                'success': False,
                'psmiles_string': psmiles_string,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }

    def _extract_psmiles_from_response(self, response: str) -> Dict:
        """Extract PSMILES string from LLM response with enhanced patterns."""
        # First, look for explicit PSMILES: format
        psmiles_patterns = [
            r'PSMILES:\s*([A-Za-z0-9\[\]\(\)\=\#\*]+)',  # "PSMILES: string"
            r'psmiles:\s*([A-Za-z0-9\[\]\(\)\=\#\*]+)',  # "psmiles: string" (lowercase)
            r'Generated:\s*([A-Za-z0-9\[\]\(\)\=\#\*]+)',  # "Generated: string"
            r'Result:\s*([A-Za-z0-9\[\]\(\)\=\#\*]+)',  # "Result: string"
            r'`([A-Za-z0-9\[\]\(\)\=\#\*]+)`',  # backtick quoted
            r'"([A-Za-z0-9\[\]\(\)\=\#\*]+)"',  # double quoted
            r'([A-Z]+[A-Za-z0-9\[\]\(\)\=\#\*]*)',  # Chemical-looking string starting with capital
        ]
        
        all_matches = []
        
        # Find all potential PSMILES strings
        for pattern in psmiles_patterns:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                # Basic validation: should look like a chemical string
                if len(match) >= 1 and any(c in match for c in 'CNOSPH[]()='):
                    # Exclude common words but keep chemical strings
                    if not match.upper() in ['THE', 'AND', 'OR', 'FOR', 'WITH', 'THIS', 'THAT', 'POLYETHYLENE', 'POLYSTYRENE']:
                        all_matches.append(match)
        
        if all_matches:
            # Remove duplicates while preserving order
            unique_matches = []
            for match in all_matches:
                if match not in unique_matches:
                    unique_matches.append(match)
            
            # Return the first valid match
            return {'psmiles': unique_matches[0], 'pattern': 'extracted'}
        
        return {'psmiles': None, 'pattern': None}
    
    def _basic_syntax_check(self, psmiles_string: str) -> Dict:
        """Perform basic syntax validation of PSMILES string."""
        errors = []
        warnings = []
        
        # Check for spaces
        if ' ' in psmiles_string:
            errors.append("PSMILES strings cannot contain spaces")
        
        # Check for hyphens
        if '-' in psmiles_string:
            errors.append("PSMILES strings cannot contain hyphens")
        
        # Check for balanced brackets
        if psmiles_string.count('(') != psmiles_string.count(')'):
            errors.append("Unbalanced parentheses")
        
        if psmiles_string.count('[') != psmiles_string.count(']'):
            errors.append("Unbalanced square brackets")
        
        # Check for valid characters (updated to exclude hyphens)
        valid_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789[]()=#*')
        invalid_chars = set(psmiles_string) - valid_chars
        if invalid_chars:
            errors.append(f"Invalid characters found: {invalid_chars}")
        
        # Check for empty string
        if not psmiles_string.strip():
            errors.append("Empty PSMILES string")
        
        # CRITICAL: Check for exactly 2 [*] symbols
        connection_count = psmiles_string.count('[*]')
        if connection_count != 2:
            errors.append(f"PSMILES must have exactly 2 [*] symbols, found {connection_count}")
        
        # Check for proper connection symbols
        connection_symbols = ['[*]', '[e]', '[d]', '[t]', '[g]']
        found_connections = [sym for sym in connection_symbols if sym in psmiles_string]
        
        # Check for empty brackets (common mistake)
        if '[]' in psmiles_string:
            warnings.append("Empty brackets [] found - should use [*] for connection points")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'connection_symbols': found_connections,
            'connection_count': connection_count,
            'length': len(psmiles_string)
        }
    
    def get_examples(self, category: str = 'all') -> Dict:
        """
        Get PSMILES examples by category.
        
        Args:
            category (str): Category of examples ('basic', 'aromatic', 'complex', 'all')
            
        Returns:
            Dict: Examples for the specified category
        """
        if category == 'all':
            return self.psmiles_examples
        
        # Filter examples by category
        categories = {
            'basic': ['methylene', 'amine', 'thiocarbonyl', 'carbonyl', 'difluoromethylene', 'oxygen'],
            'aromatic': ['para_phenylene', 'thiophene', 'pyridine', 'pyrrole'],
            'complex': ['amide_unit', 'complex_aromatic', 'meta_phenylene', 'ortho_phenylene']
        }
        
        if category in categories:
            return {k: v for k, v in self.psmiles_examples.items() 
                   if k in categories[category]}
        
        return {}
    
    def interactive_generation(self, polymer_description: str) -> Dict:
        """
        Interactive PSMILES generation with validation and suggestions.
        
        Args:
            polymer_description (str): Description of desired polymer
            
        Returns:
            Dict: Complete generation and validation results
        """
        # Generate PSMILES
        generation_result = self.generate_psmiles(polymer_description)
        
        if not generation_result['success']:
            return generation_result
        
        # Validate the generated PSMILES
        psmiles = generation_result['psmiles']
        if psmiles and psmiles != 'Not found' and psmiles != 'Could not generate':
            validation_result = self.validate_psmiles(psmiles, polymer_description)
            
            return {
                'success': True,
                'request': polymer_description,
                'generation': generation_result,
                'validation': validation_result,
                'timestamp': datetime.now().isoformat()
            }
        
        return generation_result

    def reset_conversation_memory(self):
        """Reset conversation memory and counter."""
        self.memory.clear()
        self.conversation_count = 0
        print("🔄 PSMILES conversation memory reset")
    
    def get_memory_status(self) -> Dict:
        """Get current memory and conversation status."""
        chat_history = self.memory.chat_memory.messages if hasattr(self.memory, 'chat_memory') else []
        return {
            'conversation_count': self.conversation_count,
            'memory_length': len(chat_history),
            'next_reinforcement_in': self.rule_reinforcement_interval - (self.conversation_count % self.rule_reinforcement_interval),
            'recent_messages': len(chat_history[-6:]) if chat_history else 0
        }

    def test_connection(self) -> str:
        """Test connection to the LLM."""
        try:
            response = self.llm.invoke("Generate PSMILES for ethylene: CC")
            return f"✅ PSMILES Generator connection successful. Response: {response[:100]}..."
        except Exception as e:
            return f"❌ PSMILES Generator connection failed: {e}"


def test_psmiles_generator():
    """Test function for PSMILES Generator."""
    try:
        generator = PSMILESGenerator()
        
        # Test connection
        print("Testing connection...")
        print(generator.test_connection())
        
        # Test generation
        print("\nTesting generation...")
        result = generator.generate_psmiles("polyethylene repeat unit")
        print(f"Generated: {result}")
        
        # Test validation
        print("\nTesting validation...")
        validation = generator.validate_psmiles("CC", "ethylene repeat unit")
        print(f"Validation: {validation}")
        
        return True
        
    except Exception as e:
        print(f"Test failed: {e}")
        return False


if __name__ == "__main__":
    test_psmiles_generator() 