
import argparse
from preprocessing import xml2graph, test, tex2xml
import utils.stats as stats
from models import train, search


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-tx", "--tex2xml", action="store_true", help="Default False. Download Tex equations, convert to XML and save dataset")
    parser.add_argument("-xg", "--xml2graph", action="store_true", help="Default False. Load XML, convert to graph and save dataset")
    parser.add_argument("-st", "--stats", action="store_true", help="Default False. Create stats")
    parser.add_argument("-te", "--test", action="store_true", help="Default False. Test things")
    parser.add_argument("-tr", "--train", action="store_true", help="Default False. Train things")
    parser.add_argument("-se", "--search", action="store_true", help="Default False. Search hyperparams")



    args = parser.parse_args()

    if args.tex2xml:
        tex2xml.main()

    if args.xml2graph:
        xml2graph.main()
    
    if args.stats:
        # stats.xml_occurences()
        # stats.count_text_occurences_per_tag()
        stats.extract_data_from_search()

    if args.train:
        train.main()
    
    if args.search:
        search.main()

    if args.test:
        test.main()