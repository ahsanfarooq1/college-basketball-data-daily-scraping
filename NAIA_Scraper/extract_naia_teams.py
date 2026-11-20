import json
import os
import re

def extract_naia_team_details(file_path):
    """ Extracts team_name and state from the given NAIA JSON file. """
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return []
    
    try:
        print(f"Reading {file_path}...")
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        teams_dict = {}
        
        # Regex to find state in parentheses
        state_regex = re.compile(r'\(([^)]+)\)')
        
        for record in data:
            name = record.get('team_name')
            if name:
                # Find state in parentheses
                match = state_regex.search(name)
                state = match.group(1) if match else ""
                
                # City is always empty for NAIA as per request
                city = ""
                
                # Use name + state as unique ID (since city is empty)
                unique_id = f"{name}|{state}"
                if unique_id not in teams_dict:
                    teams_dict[unique_id] = {
                        "team_name": name,
                        "city": city,
                        "state": state
                    }
                            
        # Sort by team_name
        sorted_teams = sorted(teams_dict.values(), key=lambda x: x['team_name'])
        return sorted_teams
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return []

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Input files for Mens
    mens_24_25_file = os.path.join(base_dir, "Naia_Coach_Stats_MBKB_2024_2025.json")
    mens_25_26_file = os.path.join(base_dir, "Naia_Coach_Stats_MBKB_2025_2026.json")
    
    # Input files for Womens
    womens_24_25_file = os.path.join(base_dir, "Naia_Coach_Stats_WBKB_2024_2025.json")
    womens_25_26_file = os.path.join(base_dir, "Naia_Coach_Stats_WBKB_2025_2026.json")
    
    # 2026-27 comes from the official coach-view stats (accumulation not run yet)
    mens_26_27_file = os.path.join(base_dir, "Naia_Coach_Stats_MBKB_2026_2027.json")
    womens_26_27_file = os.path.join(base_dir, "Naia_Coach_Stats_WBKB_2026_2027.json")

    # Extract Mens Teams
    print("--- Processing NAIA Mens Basketball ---")
    mens_teams_24_25 = extract_naia_team_details(mens_24_25_file)
    mens_teams_25_26 = extract_naia_team_details(mens_25_26_file)
    mens_teams_26_27 = extract_naia_team_details(mens_26_27_file)
    
    mens_final_data = {
        "2024-2025": mens_teams_24_25,
        "2025-2026": mens_teams_25_26,
        "2026-2027": mens_teams_26_27
    }
    
    # Extract Womens Teams
    print("\n--- Processing NAIA Womens Basketball ---")
    womens_teams_24_25 = extract_naia_team_details(womens_24_25_file)
    womens_teams_25_26 = extract_naia_team_details(womens_25_26_file)
    womens_teams_26_27 = extract_naia_team_details(womens_26_27_file)
    
    womens_final_data = {
        "2024-2025": womens_teams_24_25,
        "2025-2026": womens_teams_25_26,
        "2026-2027": womens_teams_26_27
    }
    
    # Output files
    output_mens = os.path.join(base_dir, "naia_mens_teams.json")
    output_womens = os.path.join(base_dir, "naia_womens_teams.json")
    
    # Save Mens
    with open(output_mens, 'w', encoding='utf-8') as f:
        json.dump(mens_final_data, f, indent=4)
    print(f"\nUpdated {output_mens}")
    print(f"Total Mens Teams: 24-25 ({len(mens_teams_24_25)}), 25-26 ({len(mens_teams_25_26)})")
    
    # Save Womens
    with open(output_womens, 'w', encoding='utf-8') as f:
        json.dump(womens_final_data, f, indent=4)
    print(f"Updated {output_womens}")
    print(f"Total Womens Teams: 24-25 ({len(womens_teams_24_25)}), 25-26 ({len(womens_teams_25_26)})")

if __name__ == "__main__":
    main()
