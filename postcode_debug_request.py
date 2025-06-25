import streamlit as st

def main():
    st.title("📍 Postcode Routeplanner")

    postcode_van = st.text_input("Postcode van (bijv. 2011AA)")
    via_string = st.text_input("Postcodes via (komma-gescheiden, bijv. 1054AA, 1011AB)")
    postcode_naar = st.text_input("Postcode naar (bijv. 2511BV)")

    if st.button("🚗 Bereken route"):
        via_postcodes = [p.strip() for p in via_string.split(",") if p.strip()]
        st.write("Van:", postcode_van)
        st.write("Via:", via_postcodes)
        st.write("Naar:", postcode_naar)
        # Hier zou je de postcode_to_coords en OSRM-oproep doen

if __name__ == "__main__":
    main()