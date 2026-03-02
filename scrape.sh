for S in 5; do 
	for R in `cat reddits ` ; do 
		./scrape.py $R --min-age 7 --delay 0.5 --min-score $S --arcticshift --start 2010-01-01 --end 2027-01-01
	done; 
done

